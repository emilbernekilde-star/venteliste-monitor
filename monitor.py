#!/usr/bin/env python3
"""Overvaager eksterne ventelister paa findbolig.nu og notificerer via ntfy.sh.

Koeres af .github/workflows/monitor.yml hvert 5. minut. Sender kun besked naar
en status rent faktisk aendrer sig - kendte statusser huskes i state.json.
"""

import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import certifi
import requests
from bs4 import BeautifulSoup

# --- Konfiguration ----------------------------------------------------------

# Tilfoej/fjern en fond ved at redigere denne liste (noegle = slug i URL'en).
FONDE = [
    "vibehusene",
    "enghaven",
    "arendal",
    "oestergaarden",
    "vestergaarden",
    "fuglevaenget",
    "hvidkildegaard",
    "solgaarden",
    "soendergaarden",
]

# Pænt visningsnavn i notifikationer. Mangler en fond her, bruges slug'en.
VISNINGSNAVNE = {
    "vibehusene": "Vibehusene",
    "enghaven": "Enghaven",
    "arendal": "Arendal",
    "oestergaarden": "Østergården",
    "vestergaarden": "Vestergården",
    "fuglevaenget": "Fuglevænget",
    "hvidkildegaard": "Hvidkildegård",
    "solgaarden": "Solgården",
    "soendergaarden": "Søndergården",
}

URL_SKABELON = "https://findbolig.nu/da-dk/udlejere/{fond}/ekstern-venteliste"

# Status-teksten ligger i denne div lige under sidens <h1>.
STATUS_SELECTOR = "div.c-article-top__heading--3"

NTFY_URL = "https://ntfy.sh/emil-venteliste-x7k2"

USER_AGENT = (
    "venteliste-monitor/1.0 (+https://github.com/emilbernekilde-star/venteliste-monitor; "
    "personlig overvaagning af ekstern venteliste; kontakt: emilbernekilde@hotmail.com)"
)

TIMEOUT = 15
TZ = ZoneInfo("Europe/Copenhagen")
HEARTBEAT_TIME = 8  # dansk lokaltid, hele timer

# Hvor ofte der tjekkes inde i én koersel, og hvor laenge koerslen lever.
# Varighed 0 betyder "tjek én gang og stop" - det er den lokale test-tilstand.
INTERVAL_SEK = int(os.environ.get("POLL_INTERVAL_SEK", "90"))
VARIGHED_SEK = int(float(os.environ.get("POLL_VARIGHED_MIN", "0")) * 60)

# Naar denne er sat, committer scriptet selv state.json + history.csv undervejs.
# Saettes kun i GitHub Actions - lokale koersler roerer aldrig git.
AUTO_COMMIT = os.environ.get("AUTO_COMMIT") == "1"

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
HISTORY_FILE = ROOT / "history.csv"
CERT_DIR = ROOT / "certs"


# --- Hjaelpere --------------------------------------------------------------

def byg_ca_bundle():
    """findbolig.nu sender kun sit eget certifikat, ikke mellemcertifikatet.

    macOS henter selv det manglende led ned og skjuler dermed fejlen, men Linux
    (og dermed GitHub Actions) afviser forbindelsen med
    'unable to get local issuer certificate'. Vi leverer derfor selv
    mellemcertifikatet fra certs/ oven i certifis roedder.
    """
    ekstra = sorted(CERT_DIR.glob("*.pem")) if CERT_DIR.is_dir() else []
    if not ekstra:
        return certifi.where()

    data = Path(certifi.where()).read_text(encoding="utf-8")
    for sti in ekstra:
        data += "\n" + sti.read_text(encoding="utf-8")

    fh = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False,
                                     encoding="utf-8")
    fh.write(data)
    fh.close()
    print(f"CA-bundle: certifi + {len(ekstra)} lokalt mellemcertifikat(er)")
    return fh.name


CA_BUNDLE = byg_ca_bundle()


def visningsnavn(fond):
    return VISNINGSNAVNE.get(fond, fond)


def url_for(fond):
    return URL_SKABELON.format(fond=fond)


def er_lukket(status):
    """En status regnes som lukket, saa laenge ordet 'lukket' indgaar."""
    return "lukket" in status.casefold()


def load_state():
    if not STATE_FILE.exists():
        return {"fonde": {}, "struktur_advarsel": {}, "sidste_heartbeat": None}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # Hellere starte forfra end at crashe - vi mister kun baseline.
        print(f"ADVARSEL: kunne ikke laese state.json ({e}) - starter med tom state")
        return {"fonde": {}, "struktur_advarsel": {}, "sidste_heartbeat": None}
    state.setdefault("fonde", {})
    state.setdefault("struktur_advarsel", {})
    state.setdefault("sidste_heartbeat", None)
    return state


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def commit_og_push():
    """Gemmer state + historik i repoet med det samme, saa intet gaar tabt hvis
    koerslen bliver afbrudt midtvejs. Maa aldrig kunne vaelte monitoren."""
    if not AUTO_COMMIT:
        return

    def git(*args, tjek=True):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=60, check=tjek)

    try:
        git("add", "state.json", "history.csv")
        if git("diff", "--cached", "--quiet", tjek=False).returncode == 0:
            return  # intet at committe
        git("commit", "-m", "Opdatér venteliste-status [skip ci]")

        for forsoeg in range(1, 4):
            if git("push", tjek=False).returncode == 0:
                print("  -> gemt i repoet")
                return
            print(f"  -> push afvist, rebaser og proever igen ({forsoeg}/3)")
            git("pull", "--rebase", "--autostash", "origin", "main", tjek=False)
            time.sleep(3)
        print("  -> ADVARSEL: kunne ikke pushe - state gemmes naeste gang")
    except Exception as e:
        print(f"  -> ADVARSEL: git-fejl ({type(e).__name__}: {e}) - fortsaetter")


def log_history(timestamp, fond, gammel_status, ny_status):
    ny_fil = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if ny_fil:
            writer.writerow(["timestamp", "fond", "gammel_status", "ny_status"])
        writer.writerow([timestamp, fond, gammel_status, ny_status])


def notify(titel, besked, prioritet="default", tags=None, klik=None):
    """Send push via ntfy. Maa aldrig kunne vaelte koerslen."""
    headers = {
        "Title": titel.encode("utf-8"),
        "Priority": prioritet,
        "User-Agent": USER_AGENT,
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if klik:
        headers["Click"] = klik
    try:
        r = requests.post(
            NTFY_URL, data=besked.encode("utf-8"), headers=headers,
            timeout=TIMEOUT, verify=CA_BUNDLE
        )
        r.raise_for_status()
        print(f"  -> notifikation sendt ({prioritet}): {titel}")
        return True
    except requests.RequestException as e:
        print(f"  -> FEJL ved afsendelse af notifikation: {type(e).__name__}: {e}")
        return False


# --- Hentning og parsing ----------------------------------------------------

def hent_status(fond, session):
    """Returnerer (status, fejl). Praecis én request, ingen retries."""
    url = url_for(fond)
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        return None, f"hentning fejlede: {type(e).__name__}: {e}"

    soup = BeautifulSoup(r.text, "html.parser")
    el = soup.select_one(STATUS_SELECTOR)
    if el is None:
        return None, f"status-elementet '{STATUS_SELECTOR}' blev ikke fundet"

    status = el.get_text(" ", strip=True)
    if not status:
        return None, f"status-elementet '{STATUS_SELECTOR}' var tomt"

    return status, None


# --- Haendelser -------------------------------------------------------------

def haandter_aendring(fond, gammel, ny, nu_iso):
    navn = visningsnavn(fond)
    url = url_for(fond)
    log_history(nu_iso, fond, gammel, ny)

    if er_lukket(gammel) and not er_lukket(ny):
        notify(
            titel=f"🚨 {navn} ER ÅBEN!",
            besked=f"{navn} er skiftet til: {ny}\n\nSkriv dig op nu:\n{url}",
            prioritet="urgent",
            tags=["rotating_light", "house"],
            klik=url,
        )
    else:
        notify(
            titel=f"Statusændring: {navn}",
            besked=f"{navn}\n{gammel}\n  ↓\n{ny}\n\n{url}",
            prioritet="default",
            tags=["arrows_counterclockwise"],
            klik=url,
        )


def haandter_parsefejl(fond, fejl, state, i_dag):
    """Advar om mulig strukturaendring - hoejst én gang pr. fond pr. dag."""
    if state["struktur_advarsel"].get(fond) == i_dag:
        print(f"  (struktur-advarsel allerede sendt i dag for {fond})")
        return False

    navn = visningsnavn(fond)
    notify(
        titel=f"Kunne ikke aflæse {navn}",
        besked=(
            f"Monitoren kunne ikke finde status på siden for {navn}.\n"
            f"Årsag: {fejl}\n\n"
            f"Sidens struktur er muligvis ændret - tjek selv:\n{url_for(fond)}"
        ),
        prioritet="low",
        tags=["warning"],
        klik=url_for(fond),
    )
    state["struktur_advarsel"][fond] = i_dag
    return True


def haandter_total_fejl(state, i_dag, fejleksempel):
    """Ingen af fondene kunne hentes - monitoren er reelt blind.

    Det er noget helt andet end at én side driller, og maa aldrig gaa stille
    forbi: uden denne besked ville monitoren kunne ligge doed i dagevis, mens
    workflowet stadig lyste groent.
    """
    if state.get("sidste_totalfejl") == i_dag:
        return False

    notify(
        titel="⚠️ Monitoren er blind",
        besked=(
            f"Ingen af de {len(FONDE)} sider kunne hentes.\n"
            f"Første fejl: {fejleksempel}\n\n"
            "Monitoren opdager IKKE en åben venteliste lige nu."
        ),
        prioritet="high",
        tags=["warning", "see_no_evil"],
    )
    state["sidste_totalfejl"] = i_dag
    return True


def haandter_heartbeat(state, nu, antal_ok, antal_total):
    """Daglig livstegn kl. 08:00 dansk tid."""
    i_dag = nu.date().isoformat()
    if state.get("sidste_heartbeat") == i_dag or nu.hour < HEARTBEAT_TIME:
        return False

    if antal_ok == antal_total:
        titel = "Venteliste-monitor kører"
        besked = f"Monitor kører, alle {antal_total} fonde tjekket."
    else:
        # Maa ikke lyde beroligende naar noget er galt.
        titel = "Venteliste-monitor kører med fejl"
        besked = (f"Monitor kører, men kun {antal_ok} af {antal_total} fonde "
                  f"kunne tjekkes.")

    notify(titel=titel, besked=besked, prioritet="low", tags=["heartbeat"])
    state["sidste_heartbeat"] = i_dag
    return True


# --- Main -------------------------------------------------------------------

def koer_runde(session, state, stoej=True):
    """Tjekker alle fonde én gang. Returnerer (state_aendret, antal_ok)."""
    nu = datetime.now(TZ)
    nu_iso = nu.isoformat(timespec="seconds")
    i_dag = nu.date().isoformat()

    aendret_state = False
    antal_ok = 0
    haendelser = []
    foerste_fejl = None

    for fond in FONDE:
        if stoej:
            print(f"{visningsnavn(fond)} ({fond}):")
        try:
            status, fejl = hent_status(fond, session)
        except Exception as e:  # sikkerhedsnet - én fond maa aldrig vaelte resten
            status, fejl = None, f"uventet fejl: {type(e).__name__}: {e}"

        if status is None:
            print(f"  FEJL ({visningsnavn(fond)}): {fejl}")
            haendelser.append(f"fejl: {fond}")
            if foerste_fejl is None:
                foerste_fejl = fejl
            # Kun uventet HTML tyder paa strukturaendring; timeout/HTTP-fejl er
            # forbigaaende og skal ikke spamme.
            if "blev ikke fundet" in fejl or "var tomt" in fejl:
                if haandter_parsefejl(fond, fejl, state, i_dag):
                    aendret_state = True
            # Gemt status roeres ikke - saa en transient fejl ikke giver
            # falsk "aendring" ved naeste koersel.
            continue

        antal_ok += 1
        gammel = state["fonde"].get(fond, {}).get("status")
        if stoej:
            print(f"  status: {status!r}")

        if gammel is None:
            if stoej:
                print("  (foerste registrering - ingen notifikation)")
            state["fonde"][fond] = {"status": status, "sidst_aendret": nu_iso}
            aendret_state = True
        elif gammel != status:
            print(f"  ÆNDRING ({visningsnavn(fond)}): {gammel!r} -> {status!r}")
            haendelser.append(f"{fond}: {status}")
            haandter_aendring(fond, gammel, status, nu_iso)
            state["fonde"][fond] = {"status": status, "sidst_aendret": nu_iso}
            aendret_state = True
        elif stoej:
            print("  uændret")

        # En struktur-advarsel er ikke laengere relevant naar parsing virker igen.
        if state["struktur_advarsel"].pop(fond, None) is not None:
            aendret_state = True

    if antal_ok == 0:
        if haandter_total_fejl(state, i_dag, foerste_fejl or "ukendt"):
            aendret_state = True
            haendelser.append("ADVARSEL: alle fonde fejlede")

    if haandter_heartbeat(state, nu, antal_ok, len(FONDE)):
        aendret_state = True
        haendelser.append("livstegn sendt")

    if not stoej:
        resume = "; ".join(haendelser) if haendelser else "ingen ændringer"
        print(f"[{nu:%H:%M:%S}] {antal_ok}/{len(FONDE)} ok - {resume}", flush=True)

    if aendret_state:
        save_state(state)
        commit_og_push()

    return aendret_state, antal_ok


def main():
    state = load_state()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.verify = CA_BUNDLE

    start = time.monotonic()
    slut = start + VARIGHED_SEK

    print(f"Venteliste-monitor - {datetime.now(TZ).isoformat(timespec='seconds')}")
    print(f"Tjekker {len(FONDE)} fonde")

    # Enkelt-tilstand: én runde og ud. Bruges lokalt og til hurtige tests.
    if VARIGHED_SEK <= 0:
        print()
        aendret, antal_ok = koer_runde(session, state, stoej=True)
        print("\nstate.json opdateret" if aendret
              else "\nIngen aendringer - state.json roert ikke")
        print(f"Faerdig: {antal_ok}/{len(FONDE)} fonde tjekket uden fejl")
        # Ingen sider hentet = monitoren er blind. Koerslen skal vaere ROED.
        return 0 if antal_ok else 1

    # Loekke-tilstand: bliv i live og tjek igen og igen, saa vi ikke er
    # afhaengige af at GitHubs cron rammer praecist.
    print(f"Løkke-tilstand: tjekker hvert {INTERVAL_SEK}. sekund "
          f"i {VARIGHED_SEK / 60:.1f} minutter\n")

    runde = 0
    runder_uden_kontakt = 0
    sidste_antal_ok = 0
    while True:
        runde += 1
        try:
            _, sidste_antal_ok = koer_runde(session, state, stoej=False)
            runder_uden_kontakt = runder_uden_kontakt + 1 if not sidste_antal_ok else 0
        except Exception as e:
            # En runde maa aldrig kunne draebe loekken - saa var vi blinde
            # resten af timen.
            print(f"  ADVARSEL: runde {runde} fejlede ({type(e).__name__}: {e})",
                  flush=True)
            runder_uden_kontakt += 1

        resterende = slut - time.monotonic()
        if resterende <= INTERVAL_SEK:
            break
        time.sleep(INTERVAL_SEK)

    print(f"\nFaerdig efter {runde} runder "
          f"({(time.monotonic() - start) / 60:.1f} min)")

    # Kom vi aldrig i kontakt med en eneste side, har koerslen ikke gjort sit
    # arbejde - saa skal den lyse roedt paa GitHub i stedet for at se sund ud.
    if runder_uden_kontakt == runde:
        print("FEJL: ingen sider kunne hentes i nogen runde")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
