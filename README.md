# venteliste-monitor

Overvåger de eksterne ventelister på [findbolig.nu](https://findbolig.nu) for 9 fonde
og sender en push-notifikation til telefonen i det sekund en liste skifter væk fra
**"Lukket for opskrivning"**.

Ventelisterne er typisk kun åbne 10–15 minutter, så pointen er at opdage åbningen
med det samme i stedet for at skulle tjekke manuelt.

## Hvad den gør

- Henter hver af de 9 sider **hvert 90. sekund** via GitHub Actions
- Læser status-teksten ud af siden (`div.c-article-top__heading--3`)
- Sammenligner med sidst kendte status i `state.json`
- **Kun ændringer** udløser en notifikation — ikke hver kørsel
- Logger alle ændringer i `history.csv`
- Sender push til ntfy-topic'et `emil-venteliste-x7k2`

### Notifikationer du kan få

| Hændelse | Prioritet | Titel |
|---|---|---|
| En liste **åbner** (status går fra "lukket" til noget andet) | `urgent` | 🚨 *Fond* ER ÅBEN! |
| Enhver anden statusændring | `default` | Statusændring: *Fond* |
| Status kunne ikke findes på siden (struktur ændret?) | `low` | Kunne ikke aflæse *Fond* |
| **Ingen** af siderne kunne hentes — monitoren er blind | `high` | ⚠️ Monitoren er blind |
| Dagligt livstegn kl. 08:00 dansk tid | `low` | Venteliste-monitor kører |

Struktur-advarslen sendes højst **én gang pr. fond pr. dag**, så en permanent
ændring på findbolig.nu ikke spammer telefonen hvert 5. minut.

Livstegnet er der, så du opdager det, hvis monitoren dør stille. Hører du intet
om morgenen, er der noget galt.

## Sådan tester du manuelt

**På GitHub:** Actions → *Venteliste-monitor* → *Run workflow*. Så kører den med
det samme i stedet for at vente på næste cron-kørsel.

**Lokalt:**

```bash
pip install -r requirements.txt
```

```bash
python monitor.py
```

Scriptet printer den parsede status for alle 9 fonde. Første kørsel uden
`state.json` registrerer bare en baseline uden at sende noget.

**Simulér en ændring** ved at rette en status i `state.json` til noget andet
(fx `"Åben for opskrivning"`) og køre igen — så opfatter monitoren det som en
ændring og sender en notifikation. Husk at siden i virkeligheden stadig er
lukket, så det tester "lukker"-retningen med normal prioritet.

Vil du teste den **urgent** åbnings-notifikation, skal den *gemte* status være
lukket og den *hentede* status være åben — det kræver, at du stubber
`hent_status()`, da den rigtige side jo er lukket.

## Sådan tilføjer/fjerner du en fond

Alt står øverst i [`monitor.py`](monitor.py):

1. Tilføj eller fjern slug'en i listen `FONDE` (slug'en er den del af URL'en, der
   står mellem `udlejere/` og `/ekstern-venteliste`)
2. Tilføj et pænt visningsnavn i `VISNINGSNAVNE` — mangler det, bruges slug'en

```python
FONDE = [
    "vibehusene",
    "enghaven",
    # ...
    "min-nye-fond",      # <- ny
]

VISNINGSNAVNE = {
    "min-nye-fond": "Min Nye Fond",
}
```

Fjerner du en fond, bliver dens gamle post liggende i `state.json`. Den gør ikke
noget, men du kan roligt slette den i hånden.

## Filer

| Fil | Formål |
|---|---|
| `monitor.py` | Hele logikken — hentning, parsing, notifikationer |
| `state.json` | Sidst kendte status pr. fond. Commit'es tilbage af workflowet |
| `history.csv` | Logbog over alle statusændringer |
| `.github/workflows/monitor.yml` | Cron hver time + manuel kørsel |
| `certs/` | Mellemcertifikat, som findbolig.nu undlader at sende — se nedenfor |

## Certifikat-problemet (vigtigt)

findbolig.nu er fejlkonfigureret: serveren sender kun sit eget certifikat og
udelader det mellemcertifikat, der binder det til en betroet rod.

macOS skjuler fejlen, fordi Apples TLS-stak selv henter det manglende led ned.
**Linux gør ikke det** — og GitHub Actions kører Linux. Derfor virkede alt på en
Mac, mens alle 9 sider fejlede på GitHub med
`unable to get local issuer certificate`.

Løsningen er `certs/rapidssl-tls-rsa-ca-g1.pem`, som scriptet lægger oven i
certifis rodcertifikater ved opstart. Alle `.pem`-filer i mappen kommer
automatisk med.

Fornyer findbolig.nu deres certifikat med en anden udsteder, holder monitoren op
med at virke. Det opdager du med det samme — så sender den "⚠️ Monitoren er
blind", og kørslen bliver rød på GitHub. Nyt mellemcertifikat hentes sådan:

```bash
echo | openssl s_client -connect findbolig.nu:443 -servername findbolig.nu 2>/dev/null | openssl x509 -noout -text | grep -A2 "Authority Information Access"
```

Hent URL'en bag "CA Issuers", konvertér til PEM med
`openssl x509 -inform DER -in hentet.crt -out certs/navn.pem`.

`state.json` skrives **kun** når noget faktisk ændrer sig, så repoet ikke får
288 commits om dagen. Ingen ændringer = ingen commit.

## Hvordan tidsstyringen virker

GitHubs cron er upålidelig — planlagte kørsler er "best effort" og kan forsinkes
20+ minutter eller droppes helt. Det blev målt i praksis her: fem `*/5`-slots i
træk passerede uden en eneste kørsel.

Derfor er opsætningen vendt om. I stedet for mange korte kørsler bruger vi **én
lang kørsel i timen, der selv tjekker hvert 90. sekund**:

```
12:00  GitHub starter kørslen
12:00  tjek → 12:01:30 → 12:03 → ... hvert 90. sekund
12:59  kørslen slutter
13:00  næste kørsel starter
```

Cron skal altså kun ramme rigtigt én gang i timen. Bliver den forsinket, tjekker
den igangværende kørsel bare videre imens. Starter næste kørsel, før den forrige
er slut, sætter `concurrency`-gruppen den i kø i stedet for at køre dobbelt.

Effektiv opdagelsestid: **~1,5 minut** mod 5–25+ minutter før.

## Kørsel på egen server (anbefalet)

GitHub Actions viste sig utilstrækkelig i praksis: over 18 timer blev kun 5 af
~18 planlagte kørsler startet, og de kom 6–33 minutter for sent. Målt dækning:
**26%**. Til et åbningsvindue på 10–15 minutter er det for lidt.

På en altid-tændt maskine forsvinder problemet, fordi der ikke er nogen
planlægger involveret — processen kører bare videre og sover 90 sekunder mellem
hvert tjek. Dækning 100%, opdagelsestid ~90 sekunder.

Installér på en frisk Ubuntu-maskine (fx Oracle Cloud Always Free):

```bash
curl -sSL https://raw.githubusercontent.com/emilbernekilde-star/venteliste-monitor/main/deploy/setup.sh | sudo bash
```

Scriptet installerer Python, henter koden til `/opt/venteliste-monitor`, opretter
en systembruger uden login og starter en systemd-tjeneste, der genstarter
automatisk ved crash og ved genstart af maskinen.

Nyttige kommandoer bagefter:

```bash
sudo journalctl -u venteliste-monitor -f
```

```bash
sudo systemctl restart venteliste-monitor
```

Hukommelsen ligger i `/var/lib/venteliste-monitor/` — altså **uden for** repoet,
så en opdatering med `git pull` aldrig kan overskrive den. Der committes intet
tilbage til GitHub i denne tilstand.

### Tilstande

Scriptet styres af to miljøvariabler:

| `POLL_VARIGHED_MIN` | Betydning | Bruges af |
|---|---|---|
| `0` (standard) | tjek én gang og stop | lokal test |
| `59` | kør i 59 minutter | GitHub Actions |
| `-1` | kør for evigt | systemd / Fly.io |

`POLL_INTERVAL_SEK` bestemmer sekunder mellem hvert tjek (standard 90).

## Ting du bør vide

- **Der er stadig et lille hul** på ca. et minut i timeskiftet, plus hvad cron
  måtte være forsinket ud over det. Fuldstændig sammenhængende dækning kræver en
  maskine, der altid er tændt (fx en lille VPS eller Fly.io).
- **Kørslen bruger næsten en time ad gangen** af GitHubs runners. Det er gratis
  og ubegrænset for offentlige repos, men det er en tung brug af en gratis
  tjeneste. Vil du være helt på den sikre side, hører den slags hjemme på noget,
  du selv betaler for.
- **Planlagte workflows slås fra** efter 60 dages inaktivitet i repoet. Monitoren
  commit'er selv ved ændringer, men i en stille periode kan det ske — det daglige
  livstegn er din advarsel.
- **ntfy-topic'et er offentligt.** Alle, der kender navnet `emil-venteliste-x7k2`,
  kan læse med. Der er ikke noget følsomt i beskederne, men skift topic-navn i
  `monitor.py` hvis det generer.
- **Høflig scraping:** ærlig User-Agent med kontaktinfo, 15 sek. timeout,
  præcis én request pr. side pr. kørsel, ingen retry-løkker.
- En fejl på én side stopper ikke de øvrige — og en midlertidig fejl overskriver
  ikke den gemte status, så du ikke får en falsk "ændring" ved næste kørsel.
