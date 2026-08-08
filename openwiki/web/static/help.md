# Hilfe

**OpenWiki** verwandelt ein PDF-Handbuch in ein durchsuchbares, editierbares Wiki
mit einem KI-Agenten und einem Wissensgraphen — vollständig lokal über **Ollama**,
ohne Cloud und ohne API-Schlüssel.

Diese Hilfe erklärt zuerst die **Bedienung der Oberfläche** und danach, wie Sie ein
**eigenes Wiki anlegen** und das Projekt **bereitstellen**.

## Aufbau der Oberfläche

Die Oberfläche hat drei Bereiche:

- **Links — Suche & Navigation:** ein Feld für die semantische Suche und der
  Navigationsbaum aller Wiki-Seiten (aus der Kapitelstruktur des Handbuchs).
- **Mitte — Inhalt:** die gerenderte Seite. Über die Reiter **Wiki**, **Hilfe**,
  **Tutorial** und **Graph** wechseln Sie die Ansicht.
- **Rechts — Agent:** ein Chat mit dem KI-Agenten, der Fragen beantwortet *und*
  Seiten bearbeiten kann.

## Seiten lesen

- Klicken Sie im Navigationsbaum auf einen Eintrag, um die Seite zu öffnen;
  Unterkapitel sind eingerückt.
- Jede Seite zeigt oben eine **Brotkrumen-Navigation** (Home › Kapitel › Seite)
  und den zugehörigen **PDF-Seitenbereich**.
- Querverweise innerhalb einer Seite sind anklickbar und springen direkt zum Ziel.

## Semantische Suche

Die Suche findet Inhalte **nach Bedeutung**, nicht nur nach Stichwörtern. Tippen
Sie eine Frage oder ein Thema ein — der Text wird mit dem Einbettungsmodell
**bge-m3** in einen Vektor umgewandelt und per Kosinus-Ähnlichkeit mit allen
Textabschnitten des Wikis verglichen.

- Jeder Treffer zeigt einen **Ähnlichkeitswert** (0–1; höher = relevanter), den
  **PDF-Seitenbereich** und einen Textausschnitt.
- Ein Klick auf einen Treffer öffnet die zugehörige Seite.
- Weil das Modell multilingual ist, funktionieren auch Umschreibungen und Synonyme.

**Beispiel-Suchen:**

- *„Wie stelle ich die Lautstärke ein?“*
- *„Effekte zu einer Kombination hinzufügen“*
- *„Unterschied zwischen Programm und Kombination“*
- *„einen Song mit dem Sequenzer aufnehmen“*

## Der Agent

Der Chat rechts wird von einem lokalen Sprachmodell
(**qwen3:30b-a3b-instruct-2507**) angetrieben. Der Agent kann **Werkzeuge**
aufrufen, um im Wiki zu suchen, Seiten zu lesen und Seiten zu **bearbeiten**.

### Fragen beantworten

Stellen Sie eine Frage in natürlicher Sprache. Der Agent sucht relevante
Abschnitte, liest sie und antwortet **ausschließlich auf Basis des Handbuchs** —
mit Quellenangaben auf die verwendeten Seiten. Findet er die Antwort nicht im
Wiki, sagt er das, statt zu raten.

**Beispiel-Fragen:**

- *„Was ist Smooth Sound Transitions (SST) und wozu dient es?“*
- *„Wie verbinde ich ein Haltepedal?“*
- *„Welche Effekt-Typen gibt es und wie werden sie geroutet?“*

### Seiten bearbeiten

Bitten Sie den Agenten, eine Seite zu ändern oder anzulegen. Dafür stehen ihm
diese Werkzeuge zur Verfügung:

| Werkzeug | Zweck |
| --- | --- |
| `search_wiki` | semantische Suche |
| `list_pages` | alle Seiten auflisten |
| `read_page` | eine Seite lesen |
| `edit_page` | eine eindeutige Textstelle ersetzen |
| `append_section` | einen Abschnitt anhängen |
| `create_page` | eine neue Seite anlegen |
| `graph_neighbors` | verwandte Seiten im Wissensgraphen auflisten |
| `find_path` | den kürzesten Beziehungspfad zwischen zwei Seiten finden |
| `find_entity` | alle Seiten finden, die ein benanntes Konzept erwähnen |

Die letzten drei Werkzeuge nutzen den **Wissensgraphen** (siehe unten) und sind
nur verfügbar, wenn ein Graph geladen ist (`find_entity` zusätzlich nur mit
Entitäten) — so kann der Agent auch nach Zusammenhängen zwischen Themen fragen,
nicht nur nach Textinhalten.

**Beispiel-Aufträge:**

- *„Erstelle eine Seite mit dem Slug ‚glossar‘ und dem Titel ‚Glossar‘ und erkläre
  kurz die Begriffe Programm, Kombination und Set List.“*
- *„Fasse die Seite ‚Verwendung der Effekte‘ in drei Sätzen zusammen und hänge sie
  als Abschnitt ‚Kurzfassung‘ an.“*
- *„Auf welchen Seiten wird der Arpeggiator erwähnt?“* (nutzt `find_entity`)
- *„Wie hängen ‚Smooth Sound Transitions‘ und ‚Verwendung der Effekte‘ zusammen?“*
  (nutzt `find_path`)

Jeder Werkzeugaufruf erscheint unter der Antwort als Chip: **·** für lesende,
**✎** für schreibende Werkzeuge. Nach einer Änderung wird die betroffene Seite
automatisch neu geladen. Ist ein Wissensgraph geladen, wird eine neue oder
geänderte Seite **sofort** in den Graphen aufgenommen (mit `Ähnlich`-Kanten) —
ohne kompletten Neuaufbau.

## Wissensgraph (Reiter „Graph")

Der Reiter **Graph** ist ein interaktiver **Graph-Explorer** rund um die aktuelle
Seite — eine zusätzliche Abstraktionsebene über dem Wiki, die die Zusammenhänge im
Handbuch sichtbar macht. Er wird von einer lokalen, eingebetteten Graph-Datenbank
(**Kuzu**) gespeist und ändert das Wiki selbst nicht.

Es gibt zwei Knotentypen: **Seiten** (Kreise) und — falls mit `--entities` gebaut —
**Begriffe/Entitäten** (Rauten, z. B. Modi, Effekte, Parameter). Das Layout ordnet
sich per Kräftesimulation selbst an.

Die Kanten (mit farbiger Legende) sind:

- **Übergeordnet / Unterseite** — die Kapitel-Hierarchie.
- **Vorherige / Nächste** — die Lesereihenfolge.
- **Ähnlich** — inhaltlich verwandte Seiten (aus den Vektor-Einbettungen).
- **Verweist auf / Verwiesen von** — die „siehe Seite N"-Querverweise des
  Handbuchs, aufgelöst auf die passende Wiki-Seite.
- **Gemeinsame Begriffe** — Seiten, die dieselben benannten Konzepte (Modi,
  Effekte, Funktionen, Parameter …) erwähnen. Nur mit `graph-build --entities`.

Bedienung:

- **Klick auf einen Knoten** *erweitert* ihn: seine Nachbarn (bzw. bei einem
  Begriff die Seiten, die ihn erwähnen) werden in den Graphen geholt — so bauen Sie
  Schritt für Schritt ein größeres Beziehungsnetz auf. Erweiterte Knoten tragen
  einen dünnen Ring.
- **Doppelklick** auf einen erweiterten Knoten *klappt* ihn wieder *ein* und
  entfernt den Teilgraphen, den er geöffnet hat — so öffnen und schließen Sie
  Teilbäume beliebig.
- **Knoten ziehen**, um den Graphen von Hand anzuordnen.
- Der zuletzt angeklickte Knoten ist der **aktive** Knoten (Akzent-Ring). Sein
  Teilgraph wird hervorgehoben (kräftigere Linien), der Rest abgeblendet.
- **Legenden-Chips** ein-/ausschalten, um Kantenarten (oder die Begriffe) ein- und
  auszublenden und dichte Ansichten zu entzerren.
- **„Seite öffnen →"** öffnet die zuletzt gewählte Seite im Reiter **Wiki**;
  **„Zurücksetzen"** kehrt zur Nachbarschaft der aktuellen Seite zurück.
- **Beschriftungen** werden in dichten Ansichten automatisch entzerrt — fahren Sie
  mit der Maus über einen Knoten, um seinen (ggf. ausgeblendeten) Namen zu sehen.

**Beispiel:** Öffnen Sie den Graphen für *„Verwendung der Effekte“*, klicken Sie
den Begriff *„Reverb“* an, um alle Seiten zu holen, die ihn erwähnen, und
doppelklicken Sie ihn wieder, um sie auszublenden.

Den Graphen erzeugt man einmalig über die Kommandozeile mit
`openwiki graph-build` (Begriffe mit `--entities`); fehlt er, ist der Reiter leer.

## Ein neues Wiki anlegen

Ein Wiki entsteht aus einem PDF in wenigen Schritten auf der **Kommandozeile**.
Jeder Befehl schreibt seine Ergebnisse nach `output/`; alle Befehle bauen
aufeinander auf. Angenommen, Ihre Datei heißt `mein-handbuch.pdf`:

**1. PDF einlesen** — extrahiert Text, Tabellen und die Kapitelstruktur:

```bash
openwiki ingest mein-handbuch.pdf
# → output/mein-handbuch.json  (+ .md)
```

**2. Wiki-Seiten erzeugen** — teilt das Dokument entlang der Kapitel in verlinkte
Markdown-Seiten:

```bash
openwiki build-wiki output/mein-handbuch.json
# → output/wiki/  (index.md, wiki.json, pages/*.md)
```

Mit `--split-level 1` entstehen gröbere (nur Kapitel-)Seiten, mit `2` (Standard)
feinere.

**3. Suchindex erstellen** — zerlegt die Seiten in Abschnitte und bettet sie ein
(benötigt Ollama, siehe unten):

```bash
openwiki index output/mein-handbuch.json
# → output/index/
```

**4. Wissensgraph bauen** (optional, aber empfohlen):

```bash
openwiki graph-build output/mein-handbuch.json
# mit Entitäten (langsamer, ein LLM-Aufruf pro Seite):
openwiki graph-build output/mein-handbuch.json --entities
# → output/graph/
```

**5. Web-Oberfläche starten:**

```bash
openwiki serve --port 8137
# dann im Browser: http://127.0.0.1:8137
```

Die Reiter **Suche**, **Chat** und **Graph** sind aktiv, sobald die jeweiligen
Artefakte (`index`, `graph`) existieren. Ändern Sie das PDF, wiederholen Sie die
Schritte 1–4 und starten Sie den Server neu.

## Bereitstellung (Deployment)

OpenWiki ist eine **lokale** Anwendung: ein schlanker Python-Webserver (nur
Standardbibliothek) plus **Ollama** für Einbettungen und den Chat. Es gibt keine
Cloud-Abhängigkeit und keinen API-Schlüssel.

**Voraussetzungen auf dem Zielrechner:**

- **Python 3.10–3.13** (unter Windows hat die Graph-Bibliothek Kuzu noch kein
  3.14-Paket).
- **[Ollama](https://ollama.com)** mit den benötigten Modellen:

  ```bash
  ollama pull bge-m3                              # Einbettungen (Suche/Graph)
  ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M  # Chat-Agent
  ```

**Installation:**

```bash
py -m venv .venv                                  # Windows
.venv\Scripts\python -m pip install -e ".[dev]"
# macOS/Linux:
# python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Danach die Schritte unter **„Ein neues Wiki anlegen“** ausführen und den Server
starten. Nützliche Schalter für `openwiki serve`:

- `--bind 0.0.0.0` — im Netzwerk erreichbar machen (Standard: nur `127.0.0.1`).
- `--port 8137` — Port festlegen.
- `--host http://…:11434` — Adresse eines Ollama-Servers auf einem anderen Rechner.
- `--dry-run` — der Agent *schlägt* Änderungen nur vor, schreibt sie aber nicht.

**Sicherheit:** Der Server hat **keine Authentifizierung** und der Chat-Agent kann
Dateien im `pages`-Ordner schreiben. Betreiben Sie ihn daher nur auf `localhost`
oder in einem vertrauenswürdigen Netzwerk; wenn Sie ihn nach außen öffnen, setzen
Sie einen Reverse-Proxy mit Zugriffsschutz davor (und ziehen Sie `--dry-run` in
Betracht). Alle Daten liegen im `output/`-Ordner — zum Umziehen genügt es, diesen
Ordner mitzunehmen.

## Tipps

- **Enter** sendet die Chat-Nachricht, **Umschalt+Enter** fügt eine neue Zeile ein.
- Für Bearbeitungen hilft es, **Seite (Slug oder Titel)** und die gewünschte
  Änderung klar zu benennen.
- Schreibzugriffe sind auf den `pages`-Ordner des Wikis beschränkt; mit
  `--dry-run` werden Änderungen nur *vorgeschlagen*, nicht geschrieben.

## Datenschutz & lokaler Betrieb

Suche und Chat laufen vollständig auf Ihrem Rechner über Ollama (Standard:
`http://localhost:11434`). Es werden keine Inhalte an externe Dienste gesendet.

## Fehlerbehebung

- **„Could not reach Ollama“ / Suche oder Chat schlagen fehl:** Läuft der
  Ollama-Server? Sind die Modelle geladen (`ollama pull bge-m3`,
  `ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M`)?
- **Keine Suchergebnisse / Suche deaktiviert:** Der Index fehlt. Erzeugen Sie ihn
  mit `openwiki index …` und starten Sie den Server neu.
- **Graph-Reiter leer / „nicht verfügbar":** Der Graph fehlt. Erzeugen Sie ihn mit
  `openwiki graph-build …` und starten Sie den Server neu.
- **Reiter „Begriffe“ fehlt im Graph:** Der Graph wurde ohne `--entities` gebaut.
- Gestartet wird der Server über die Kommandozeile: `openwiki serve --port 8137`.
