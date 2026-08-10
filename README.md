# venteliste-monitor

Overvåger de eksterne ventelister på [findbolig.nu](https://findbolig.nu) for 9 fonde
og sender en push-notifikation til telefonen i det sekund en liste skifter væk fra
**"Lukket for opskrivning"**.

Ventelisterne er typisk kun åbne 10–15 minutter, så pointen er at opdage åbningen
med det samme i stedet for at skulle tjekke manuelt.

## Hvad den gør

- Henter hver af de 9 sider hvert 5. minut via GitHub Actions
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
| `.github/workflows/monitor.yml` | Cron hvert 5. minut + manuel kørsel |

`state.json` skrives **kun** når noget faktisk ændrer sig, så repoet ikke får
288 commits om dagen. Ingen ændringer = ingen commit.

## Ting du bør vide

- **Cron er ikke præcis.** GitHub Actions kører planlagte jobs "best effort" og
  kan forsinke dem i travle perioder. Med et 10–15 minutters vindue kan du i
  uheldige tilfælde nå at misse en åbning. Kører du efter noget mere garanteret,
  skal det køre et sted, du selv styrer.
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
