# Portfolio dashboard — GitHub haalt zelf de koersen op

Deze opzet lost het datalaadprobleem definitief op. GitHub draait op de achtergrond
elke 15 minuten een scriptje dat de koersen bij Yahoo ophaalt en wegschrijft naar
`data.json`. Je dashboard leest dat bestand. **Geen proxy, geen CORS, werkt op
Windows én Android.**

## Wat zit erin
- `index.html` — het dashboard
- `update_prices.py` — haalt de koersen op (draait op GitHub, niet op jouw pc)
- `.github/workflows/update-prices.yml` — laat GitHub dat script automatisch draaien
- `tickers.txt` — lijst van aandelen die opgehaald worden
- `data.json` — hierin komt de opgehaalde koersdata (begint leeg)

---

## Installeren (eenmalig, ± 10 min)

### 1. Repository aanmaken
- Ga naar **github.com** → **+** rechtsboven → **New repository**
- Naam: bijvoorbeeld `portfolio`
- Kies **Public**  (bij Private werkt GitHub Pages alleen op betaalde plannen)
- Klik **Create repository**

### 2. Bestanden uploaden
- Klik **Add file** → **Upload files**
- Sleep **alle** bestanden hieruit erin, met behoud van de map `.github`
  - Makkelijkst: sleep de hele inhoud van deze map in het uploadvak.
  - Belangrijk: de map `.github/workflows/` met het `.yml`-bestand moet mee.
    Als slepen de mappenstructuur niet meepakt, zie stap 2b.
- Klik **Commit changes**

**2b. Als de `.github`-map niet meekomt via slepen:**
- Klik **Add file** → **Create new file**
- Typ als bestandsnaam exact: `.github/workflows/update-prices.yml`
  (de schuine strepen maken automatisch de mappen aan)
- Plak de inhoud van het meegeleverde `update-prices.yml` erin → **Commit**

### 3. Zet de Action aan en draai hem één keer
- Ga naar de tab **Actions** bovenin je repository
- Zie je "Workflows aren't being run on this repository"? Klik de groene knop
  om ze in te schakelen.
- Klik links op **Update stock prices** → rechts **Run workflow** → **Run workflow**
- Wacht ~1 minuut en ververs. Er verschijnt een groen vinkje als het lukte.
  In je bestandenlijst is `data.json` nu gevuld met koersen.

> Werkt de run niet? Open de run, klik op de stap die rood is en kijk naar de melding.
> Stuur mij die tekst, dan help ik verder.

### 4. Zet GitHub Pages aan (voor de weblink)
- Tab **Settings** → links **Pages**
- Branch: **main**, map **/(root)** → **Save**
- Na 1–2 min verschijnt bovenaan je link: `https://JOUWNAAM.github.io/portfolio/`

Die link werkt op je pc én je telefoon. Op Android: open in Chrome →
menu → **Toevoegen aan startscherm** voor een app-icoon.

---

## Een aandeel toevoegen
1. Open `tickers.txt` in je repository (klik erop → potloodje om te bewerken)
2. Zet de nieuwe ticker op een eigen regel (bv. `ASML.AS`) → **Commit**
3. De ticker wordt bij de volgende run opgehaald (of draai de Action handmatig)
4. Voeg het aandeel ook in het dashboard zelf toe (knop **+ Positie**) met je
   aankoopprijs — dat bepaalt je resultaat en het koopsignaal

Achtervoegsels: Xetra = `.DE` (euro) · Amsterdam = `.AS` · NASDAQ = geen (dollar)

---

## Hoe vaak vernieuwt het?
Elke 15 minuten haalt GitHub nieuwe koersen op. Het dashboard zelf ververst ook,
en toont bovenaan "data van <tijdstip>" zodat je ziet hoe vers het is. Voor het
opsporen van koopsignalen (20% onder de 52-weeks top) is dat ruim vaak genoeg.

## Belangrijk om te weten
- De koersen kunnen ~15 min oud zijn — prima voor dit doel, niet voor daytrading.
- Je posities (aankoopprijs, aantal) worden per apparaat in de browser bewaard;
  de tickerlijst voor het ophalen staat centraal in `tickers.txt`.
- GitHub Actions is gratis voor publieke repositories.
