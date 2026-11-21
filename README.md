# 📺 Gidsrepareerder

Maakt elke nacht een betere tv-gids voor je IPTV-lijst. Zenders als Ziggo Sport,
ESPN, Viaplay en de Engelse zenders krijgen hun programma's uit betere openbare
gidsen. Alle andere zenders houden de gids van je aanbieder.

## Eenmalig instellen

1. **Nieuwe repository** maken op GitHub, bijvoorbeeld `gids`. Zet hem op **Public**:
   alleen dan is GitHub Pages gratis. Je wachtwoord blijft toch geheim (zie stap 3).
2. **Bestanden uploaden:** *Add file → Upload files* en kies `epg_fix.py`,
   `bronnen.txt`, `koppelingen.txt` en `README.md`. Klik op *Commit changes*.
3. **Geheim toevoegen:** *Settings → Secrets and variables → Actions → New repository secret*
   - Name: `M3U_URL`
   - Secret: je M3U-link, bij Xtream in deze vorm:
     `http://SERVER:POORT/get.php?username=NAAM&password=WACHTWOORD&type=m3u_plus&output=ts`
4. **Pages aanzetten:** *Settings → Pages → Source:* kies **GitHub Actions**.
5. **Workflow toevoegen:** *Add file → Create new file*. Typ als naam precies
   `.github/workflows/gids.yml` en plak de inhoud van `gids.yml`. Klik op *Commit changes*.
6. **Eerste keer draaien:** tabblad *Actions → Gids repareren → Run workflow*.
   Na een paar minuten verschijnt er een groen vinkje.

## Gebruiken

- Gidslink voor TV-Mate (EPG-bron): `https://JOUWNAAM.github.io/gids-TiviMate/epg.xml.gz`
- Rapport: `https://JOUWNAAM.github.io/gids-TiviMate/` laat zien wat gerepareerd is en wat niet.

Vervang `JOUWNAAM` door je GitHub-naam en `gids-TiviMate` door de naam van je repository.

## Zender niet gevonden?

Open het rapport. Bij **Niet gevonden** staan suggesties met een code. Zet in
`koppelingen.txt` een regel:

```
NL: Ziggo Sport 2 HD = Ziggo.Sport.2.nl
```

Na het opslaan draait alles vanzelf opnieuw.

## Goed om te weten

- Je link en wachtwoord komen nooit in de gids, het rapport of de logs.
- GitHub zet nachtelijke taken uit als er 60 dagen niets aan de repository is
  veranderd. Je krijgt dan eerst een mail, en met één klik staat het weer aan.
- Mislukt stap 1 ("zenderlijst kon niet worden opgehaald")? Dan blokkeert je
  aanbieder misschien de servers van GitHub. Laat het me weten, dan zoeken we
  een andere oplossing.
