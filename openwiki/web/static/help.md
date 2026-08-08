# Hilfe

**OpenWiki** verwandelt ein PDF-Handbuch in ein durchsuchbares, editierbares Wiki
mit einem KI-Agenten — vollständig lokal über **Ollama**, ohne Cloud und ohne
API-Schlüssel.

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
Sie eine Frage oder ein Thema ein (z. B. *„Wie stelle ich die Lautstärke ein?“*) —
der Text wird mit dem Einbettungsmodell **bge-m3** in einen Vektor umgewandelt und
per Kosinus-Ähnlichkeit mit allen Textabschnitten des Wikis verglichen.

- Jeder Treffer zeigt einen **Ähnlichkeitswert** (0–1; höher = relevanter), den
  **PDF-Seitenbereich** und einen Textausschnitt.
- Ein Klick auf einen Treffer öffnet die zugehörige Seite.
- Weil das Modell multilingual ist, funktionieren auch Umschreibungen und Synonyme.

## Der Agent

Der Chat rechts wird von einem lokalen Sprachmodell
(**qwen3:30b-a3b-instruct-2507**) angetrieben. Der Agent kann **Werkzeuge**
aufrufen, um im Wiki zu suchen, Seiten zu lesen und Seiten zu **bearbeiten**.

### Fragen beantworten

Stellen Sie eine Frage in natürlicher Sprache. Der Agent sucht relevante
Abschnitte, liest sie und antwortet **ausschließlich auf Basis des Handbuchs** —
mit Quellenangaben auf die verwendeten Seiten. Findet er die Antwort nicht im
Wiki, sagt er das, statt zu raten.

### Seiten bearbeiten

Bitten Sie den Agenten, eine Seite zu ändern — z. B. *„Ergänze die Seite X um
einen Abschnitt Y“* oder *„Erstelle eine neue Seite …“*. Dafür stehen ihm diese
Werkzeuge zur Verfügung:

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

Diese Werkzeuge nutzen den **Wissensgraphen** (siehe unten) und sind nur
verfügbar, wenn ein Graph geladen ist (`find_entity` zusätzlich nur mit
Entitäten) — so kann der Agent auch nach Zusammenhängen zwischen Themen fragen,
nicht nur nach Textinhalten.

Jeder Werkzeugaufruf erscheint unter der Antwort als Chip: **·** für lesende,
**✎** für schreibende Werkzeuge. Nach einer Änderung wird die betroffene Seite
automatisch neu geladen.

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
  Effekte, Funktionen, Parameter …) erwähnen. Nur vorhanden, wenn der Graph mit
  Entitäten gebaut wurde (`graph-build --entities`).

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
  Teilgraph wird hervorgehoben (kräftigere Linien), der Rest abgeblendet — so sehen
  Sie auf einen Blick, welchen Teilbaum Sie gerade bearbeiten.
- **Legenden-Chips** ein-/ausschalten, um Kantenarten (oder die Begriffe) ein- und
  auszublenden und dichte Ansichten zu entzerren.
- **„Seite öffnen →"** öffnet die zuletzt gewählte Seite im Reiter **Wiki**;
  **„Zurücksetzen"** kehrt zur Nachbarschaft der aktuellen Seite zurück.
- **Beschriftungen** werden in dichten Ansichten automatisch entzerrt — fahren Sie
  mit der Maus über einen Knoten, um seinen (ggf. ausgeblendeten) Namen zu sehen.

Den Graphen erzeugt man einmalig über die Kommandozeile mit
`openwiki graph-build` (Begriffe mit `--entities`); fehlt er, ist der Reiter leer.

## Tipps

- **Enter** sendet die Chat-Nachricht, **Umschalt+Enter** fügt eine neue Zeile ein.
- Für Bearbeitungen hilft es, **Seite (Slug oder Titel)** und die gewünschte
  Änderung klar zu benennen.
- Schreibzugriffe sind auf den `pages`-Ordner des Wikis beschränkt; mit dem
  Server-Schalter `--dry-run` werden Änderungen nur *vorgeschlagen*, nicht
  geschrieben.

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
- Gestartet wird der Server über die Kommandozeile: `openwiki serve --port 8137`.
