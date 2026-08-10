#!/usr/bin/env python3
"""Overvaager eksterne ventelister paa findbolig.nu og notificerer via ntfy.sh.

Koeres af .github/workflows/monitor.yml hvert 5. minut. Sender kun besked naar
en status rent faktisk aendrer sig - kendte statusser huskes i state.json.
"""

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
HISTORY_FILE = ROOT / "history.csv"


# --- Hjaelpere --------------------------------------------------------------

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
            NTFY_URL, data=besked.encode("utf-8"), headers=headers, timeout=TIMEOUT
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


def haandter_heartbeat(state, nu, antal_ok, antal_total):
    """Daglig livstegn kl. 08:00 dansk tid."""
    i_dag = nu.date().isoformat()
    if state.get("sidste_heartbeat") == i_dag or nu.hour < HEARTBEAT_TIME:
        return False

    notify(
        titel="Venteliste-monitor kører",
        besked=f"Monitor kører, alle {antal_ok} af {antal_total} fonde tjekket.",
        prioritet="low",
        tags=["heartbeat"],
    )
    state["sidste_heartbeat"] = i_dag
    return True


# --- Main -------------------------------------------------------------------

def main():
    nu = datetime.now(TZ)
    nu_iso = nu.isoformat(timespec="seconds")
    i_dag = nu.date().isoformat()

    state = load_state()
    aendret_state = False
    antal_ok = 0

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print(f"Venteliste-monitor - {nu_iso}")
    print(f"Tjekker {len(FONDE)} fonde\n")

    for fond in FONDE:
        print(f"{visningsnavn(fond)} ({fond}):")
        try:
            status, fejl = hent_status(fond, session)
        except Exception as e:  # sikkerhedsnet - én fond maa aldrig vaelte resten
            status, fejl = None, f"uventet fejl: {type(e).__name__}: {e}"

        if status is None:
            print(f"  FEJL: {fejl}")
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
        print(f"  status: {status!r}")

        if gammel is None:
            print("  (foerste registrering - ingen notifikation)")
            state["fonde"][fond] = {"status": status, "sidst_aendret": nu_iso}
            aendret_state = True
        elif gammel != status:
            print(f"  ÆNDRING: {gammel!r} -> {status!r}")
            haandter_aendring(fond, gammel, status, nu_iso)
            state["fonde"][fond] = {"status": status, "sidst_aendret": nu_iso}
            aendret_state = True
        else:
            print("  uændret")

        # En struktur-advarsel er ikke laengere relevant naar parsing virker igen.
        if state["struktur_advarsel"].pop(fond, None) is not None:
            aendret_state = True

    if haandter_heartbeat(state, nu, antal_ok, len(FONDE)):
        aendret_state = True

    if aendret_state:
        save_state(state)
        print("\nstate.json opdateret")
    else:
        print("\nIngen aendringer - state.json roert ikke")

    print(f"Faerdig: {antal_ok}/{len(FONDE)} fonde tjekket uden fejl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
