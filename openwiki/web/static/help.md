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
  Bei einem Korpus aus **mehreren Quellen** erscheint darüber ein **Quellen-/Bücher-Filter**,
  der Navigationsbaum und Suche auf eine Quelle (bzw. ein Buch) einschränkt; Suchtreffer
  zeigen zudem ihre Herkunftsquelle an.
- **Mitte — Inhalt:** die gerenderte Seite. Über die Reiter **Projekt**, **Wiki**,
  **Graph**, **Begriffe**, **Analyse**, **Gedächtnis**, **Evaluation**, **System**, **Tutorial**
  und **Hilfe** wechseln Sie die Ansicht. (Reiter, deren Artefakte fehlen — z. B. ohne Graph
  oder ohne Gedächtnis — zeigen einen Hinweis statt Inhalt.)
- **Rechts — Agent:** ein Chat mit dem KI-Agenten, der Fragen beantwortet *und*
  Seiten bearbeiten kann.

Über die Kopfzeile lassen sich **Seitenleiste** (☰) und **Agent-Panel** (💬) ein- und
ausblenden (Fokus-/Lesemodus) und das **Design** (🌙/☀️) umschalten — alle Einstellungen
werden gespeichert.

## Seiten lesen

- Klicken Sie im Navigationsbaum auf einen Eintrag, um die Seite zu öffnen;
  Unterkapitel sind eingerückt.
- Jede Seite zeigt oben eine **Brotkrumen-Navigation** (Home › Kapitel › Seite)
  und den zugehörigen **PDF-Seitenbereich**.
- **Verweise im Fließtext** („Abschnitt 1.6", „Seite 42", „Kapitel 2") sind anklickbar und öffnen
  die Seite, auf die der Wissensgraph sie aufgelöst hat. Ältere Graphen erhalten diese Links mit
  `openwiki references` — ohne Neuaufbau.
- Unter jeder Seite erscheint ein **„Verwandte Seiten"**-Bereich, der die Vernetzung aus
  dem Wissensgraphen als anklickbare Links zeigt: **Verweise** und **Erwähnt in**
  (Rückverweise), **Verwandte Themen** (getypte Beziehungen), **Ähnliche Seiten**,
  **Gemeinsame Begriffe** und — bei Code-Projekten — **Oft zusammen geändert** (Dateien, die laut git
  meist mit dieser geändert wurden) — so wird jede Seite zum Knotenpunkt, ohne den Quelltext zu ändern.
- Im Fließtext wird die **erste Erwähnung** jedes bekannten Begriffs (gepunktet unterstrichen)
  automatisch verlinkt — ein Klick öffnet den Begriff im Reiter **Begriffe**.

## Semantische Suche

Die Suche findet Inhalte **nach Bedeutung**, nicht nur nach Stichwörtern. Tippen
Sie eine Frage oder ein Thema ein — der Text wird mit dem Einbettungsmodell
**bge-m3** in einen Vektor umgewandelt und per Kosinus-Ähnlichkeit mit allen
Textabschnitten des Wikis verglichen.

- Jeder Treffer zeigt einen **Ähnlichkeitswert** (0–1; höher = relevanter), den
  **PDF-Seitenbereich** und einen Textausschnitt.
- Ein Klick auf einen Treffer öffnet die zugehörige Seite.
- Weil das Modell multilingual ist, funktionieren auch Umschreibungen und Synonyme.
- Die Option **Hybrid (BM25 + Vektor)** mischt die Vektorsuche mit einer lexikalischen
  BM25-Suche — hilfreich für exakte Begriffe, Bezeichner und Abkürzungen.
- Über den 🌙/☀️-Schalter oben rechts wechseln Sie zwischen hellem und dunklem Design
  (die Wahl wird gespeichert).

**Beispiel-Suchen:**

- *„Wie stelle ich die Lautstärke ein?“*
- *„Effekte zu einer Kombination hinzufügen“*
- *„Unterschied zwischen Programm und Kombination“*
- *„einen Song mit dem Sequenzer aufnehmen“*

## Der Agent

Der Chat rechts wird von einem lokalen Sprachmodell
(**qwen3:30b-a3b-instruct-2507**) angetrieben. Oben schalten Sie zwischen zwei Modi um:

- **Agent** — der mehrschrittige Werkzeug-Agent: sucht, liest und **bearbeitet** Seiten
  (siehe unten).
- **Ask** — reine **Frage-Antwort (RAG)**, nur lesend, mit Quellenangaben; die Antwort
  wird **Token für Token gestreamt** (erscheint während der Generierung). Eine
  Schalterleiste steuert das Retrieval: **GraphRAG** (Seeds entlang der Graphkanten
  erweitern), **Hybrid** (BM25 + Vektor), **Re-rank** (LLM-Neusortierung), **Global**
  (Antwort aus den Themen-Zusammenfassungen) und **k** (Anzahl der Seed-Treffer). Jede
  Antwort zeigt anklickbare Quellen-Chips — **Seed** (semantischer Treffer) vs.
  **+Graph** (per Graph ergänzt) — plus eine ⏱-Zeile mit Dauer und Tokens. Das ist die
  Browser-Entsprechung von `openwiki ask` auf der Kommandozeile.

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
- **Beziehung** — Seiten, die eine **getypte Beziehung** zwischen ihren Begriffen
  verbindet (Subjekt–Prädikat–Objekt, z. B. „X *besteht aus* Y"). Nur mit
  `graph-build --relations` — macht aus Ko-Erwähnung einen echten Graphen.
- **Verstärkt** — „genutzte" Verbindungen aus der Nutzungs­erinnerung (nur im
  Second-Brain-Modus, mit Halbwertszeit gealtert).

Mit `graph-build --resolve-entities` werden Schreibvarianten desselben Begriffs zu
**kanonischen** Entitäten mit Aliassen zusammengeführt — so findet die Suche nach
einer Abkürzung auch die ausgeschriebene Form. Sind **Communities** (Themen) berechnet
(`openwiki communities`), färbt der Reiter die Seitenknoten **nach Thema** ein
(umschaltbar über „Themenfarben") und zeigt eine Themen-Legende.

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

## Projekt (Reiter „Projekt")

Wurde der Server in einem **Projekt** gestartet (einem Ordner mit `openwiki.toml`,
siehe „Ein neues Wiki anlegen"), zeigt der Reiter **Projekt** dessen Zustand — und
der Projektname erscheint als Abzeichen (📁) oben in der Kopfzeile. Angezeigt wird:

- **Quellen** — die deklarierten Eingabedokumente (mit ✓, falls vorhanden).
- **Build-Status** je Stufe (`ingest`, `wiki`, `index`, `graph`, `memory`) als
  **aktuell**, **veraltet** oder **fehlt**, mit Kennzahlen (Seiten, Chunks …) sowie
  **Dauer** und **LLM-Verbrauch** (Tokens) der letzten Ausführung. So sehen Sie auf
  einen Blick, was ein `openwiki build` neu bauen würde und was es gekostet hat.
- **Modelle & Einstellungen** — Einbettungs- und Chat-Modell, Ollama-Host,
  `split_level`, Chunk-Größe und die **Begriffs-Ontologie** aus dem Manifest, plus
  Live-Graphstatistiken (Knoten/Kanten, Verteilung der Entitätstypen).
- **Themen (Communities)** — sind Communities berechnet (`openwiki communities`),
  erscheinen sie hier als Karten (Thema + Größe + LLM-Zusammenfassung). Über die
  **Globale Suche** stellen Sie eine *thematische* Frage („Wie hängen die Hauptthemen
  zusammen?"), die aus den Zusammenfassungen beantwortet wird — das kann die reine
  Abschnitts-Suche nicht.
- **Registrierte Projekte** — alle per `openwiki project add` bekannten Projekte
  (das aktive ist mit ★ markiert).

Läuft der Server ohne Projekt (mit direkten `--wiki`/`--index`-Pfaden), weist der
Reiter darauf hin und verweist auf `openwiki init`.

## Begriffe (Reiter „Begriffe")

Der Reiter **Begriffe** ist ein durchsuchbarer Browser der **kanonischen Entitäten** des
Wissensgraphen. Links eine nach Erwähnungen sortierte Liste (mit Suchfeld + Typ-Filter),
rechts das Detail des gewählten Begriffs:

- die **Beschreibung** und die **Aliasse** (aus der Entitäts-Auflösung, `--resolve-entities`) —
  so finden Sie einen Begriff auch über eine Schreibvariante oder Abkürzung;
- die **Seiten**, die ihn erwähnen (anklickbar → öffnet die Wiki-Seite);
- die **getypten Beziehungen** (`--relations`): Prädikat + Richtung + verbundener Begriff —
  ein Klick springt zu *dessen* Detail, sodass Sie den Beziehungsgraphen entlangwandern können.

Fehlt die Entitätsschicht, weist der Reiter darauf hin (`graph-build --entities`).

## Analyse (Reiter „Analyse")

Der Reiter **Analyse** vermisst die **Struktur und Organisation** des Wissens selbst
— er behandelt die beiden Darstellungen desselben Korpus (den symbolischen **Graphen**
und den **semantischen Raum** der Einbettungen) als *ein* Objekt. Er hat drei Unterreiter
(alles nur lesend, ohne Ollama-Aufruf):

**Kopplung** — *Wo stimmen Graph und Einbettungen überein (redundant), und wo fügt der
Graph Struktur hinzu, die reine Ähnlichkeit nicht sieht?*

- Eine **Kennzahlentabelle** je Kantentyp: mittlerer Kosinus der Endpunkte gegen ein
  Zufallspaar (**vs. Null**) und der **Overlap** mit den Einbettungs-Nachbarn.
- Die **Graph-Reichweite** (Schlagzeile): der Anteil der Nicht-Ähnlichkeitskanten, die
  Seiten verbinden, die der Embedder *nicht* als Nachbarn einstufen würde — so viel
  nicht-semantische Struktur kodiert der Graph.
- **Community-Kohärenz** (Silhouette + ARI) und eine **semantische Karte**: die Seiten
  in 2D projiziert, nach Thema eingefärbt, mit überlagerten Graphkanten (Kantentypen
  ein-/ausschaltbar; Klick auf einen Knoten öffnet die Seite). Über der Karte wählen Sie
  die **Projektion** (PCA/UMAP) und einen **Fokus** auf ein einzelnes Thema (der Rest wird
  abgeblendet).

**Lücken** — eine umsetzbare **To-do-Liste** zur Verbesserung des Wikis: *fehlende
Querverweise* (Seiten, die dieselben Begriffe erwähnen, sich aber nicht zitieren),
*Beinahe-Duplikate* (fast identische Einbettungen), *isolierte Seiten* (semantische
Ausreißer + strukturelle Waisen) und *zusammenführbare Begriffe* (gleicher Typ, ähnliche
Namen — Kandidaten für `--resolve-entities`). Seitenverweise sind anklickbar.

**Dynamik** — die **Gedächtnis-Dynamik** (nur im Second-Brain-Modus mit erfassten
Sitzungen): Revision (überschriebene Fakten), Konsolidierung (Anteil in Themen),
Temperatur (heiß/warm/kalt nach Aktualität + Konfidenz), Breite — und **Gedächtnis über die Zeit**:
je Tag die gelernten Fakten (über der Nulllinie) und die, die nicht mehr aktuell sind (darunter:
geschlossen — die Welt hat sich geändert; zurückgezogen — korrigiert; vergessen — vom Schlaf-Durchlauf
archiviert), dazu als Linie die Zahl der aktuellen Fakten. Die Tage sind die, an denen etwas *gesagt*
wurde — ein nachträglich erfasster Tag behält sein Datum. Ein Klick auf einen Tag listet seine Fakten;
ein Klick auf einen Fakt öffnet ihn im Reiter **Gedächtnis**. Über mehr als 120 Tage zeigt das
Diagramm Wochen. Darunter, eingeklappt: das Wachstum je Sitzung.

Dieselben Auswertungen gibt es auf der Kommandozeile: `openwiki analyze [coupling|gaps|memory]`
und `analyze --compare` (zwei Wissensstände vergleichen). Die Kohärenz-Kennzahl und die
UMAP-Karte benötigen das optionale Zusatzpaket `pip install "owiki[analysis]"` (sonst PCA +
Kernkennzahlen).

## Gedächtnis (Reiter „Gedächtnis")

Im **Second-Brain-Modus** (`[memory] enabled = true` im Manifest) führt der Graph
zusätzlich einen **Gedächtnis-Tier**: Fakten, die aus Sitzungen *erinnert* werden
(`openwiki remember`) und in einer späteren Sitzung *wieder abgerufen* werden
(`recall`) — inklusive Widerspruchs­behandlung (ein neuerer Fakt **überschreibt** einen
älteren) und einer „Schlaf"-Konsolidierung zu Themen. Der Reiter zeigt:

- die **Identität** (DNA) des Projekts und Kennzahlen (Sitzungen / Fakten / überholt /
  zurückgezogen / geplant / vergessen / Themen);
- ein **Recall/Kontext-Feld** — geben Sie eine Frage ein:
  - **Abrufen** zeigt die relevantesten Fakten, jeden mit seiner **Bewertung** als Formel:
    Ähnlichkeit (cos) × Konfidenz × Aktualität × Herkunft = Score. Die Aktualität zählt nur bei
    Arten, die von selbst veralten (Pläne, Zahlen, Lücken, Versionen, laufende Zustände), sonst
    ist sie 1; Material aus besprochenen Dokumenten zählt × 0,75. Dazu kommen die **Abrufhilfen**:
    ein Stichwortabgleich (BM25) und der Zeitraum, den die Frage nennt — sie dürfen Fakten aus
    den nächstbesten nach oben holen („hereingeholt · #13" = nach Score erst Platz 13), ändern
    aber nie die Reihenfolge. Was dafür weichen musste, steht unter **Verdrängt**;
  - **Hook-Vorschau** zeigt, was der Hook dieser Eingabe jetzt in den Prompt einfügen würde — den
    Text samt Kopfzeile, Zeichen gegen das Budget, jeden abgerufenen Fakt mit „im Text" oder
    „gekürzt" (Budget aufgebraucht) und seiner Bewertung. Bei einer Routine-Eingabe („push", ein
    Slash-Befehl) sagt sie, dass der Hook nichts einfügt. Sie zeigt den Anfang einer Sitzung;
    danach gibt der Hook jeden Fakt nur einmal;
  - **Verlauf** zeigt die ganze Geschichte der passendsten Fakten (jedes Gültigkeitsintervall,
    wann erfasst, aus welcher Sitzung);
- zwei **Zeitpunkt-Felder**: **Stand am** — welche Fakten *galten* an diesem Tag
  (Gültigkeitszeit, `recall --as-of`); **Wissensstand vom** — was OpenWiki an diesem Tag
  *gespeichert hatte*, vor späteren Korrekturen (Transaktionszeit, `recall --known-at`);
  „heute" setzt beide zurück;
- die **Fakten** — alle, nicht nur die ersten: ein Suchfeld (jedes Wort muss vorkommen) und Filter
  für Status (aktuell / alle / überholt / zurückgezogen / geplant / vergessen), Quelle (Nutzer /
  Assistent / Material), Sitzung und Art (die Arten, die von selbst veralten: Pläne, Zahlen, Lücken,
  Versionen, laufende Zustände), dazu Sortierung und Blättern. **Liste | Karte** schaltet auf die
  **Gedächtniskarte**: alle Fakten nach ihrer Bedeutung in zwei Dimensionen — nah beieinander heißt
  ähnlich. Was die Filter wählen, ist farbig nach Thema (grau: ohne Thema, hohl: nicht mehr aktuell),
  die übrigen aktuellen Fakten bleiben als blasse Punkte stehen; die Karte verschiebt sich nicht, wenn
  die Filter wechseln. So sieht man, wo ein Suchbegriff oder ein Thema liegt — und ob ein Thema
  zusammenhält oder einzelne Fakten weit abseits liegen. Mausrad zoomt, Ziehen verschiebt, Doppelklick
  setzt zurück, ein Klick öffnet den Fakt. Über der Karte steht, wie viel sie von den echten
  Nachbarschaften erhält (t-SNE: etwa die Hälfte der 10 nächsten Nachbarn eines Fakts). Ohne
  scikit-learn (`pip install openwiki[analysis]`) bleibt nur PCA, und die Karte sagt, dass sie dann
  kaum taugt;
- die **Themen** als kompakte, durchsuchbare Liste — ein Klick auf ein Thema zeigt nur seine Fakten
  (✕ hebt den Filter auf), „▸" klappt die Zusammenfassung auf;
- ein Klick auf einen Fakt öffnet seine **Details**: der **Verlauf dieses Attributs** als Balken auf
  einer Zeitachse (jeder Wert mit seinem Gültigkeitsintervall, die Linie markiert heute), Quelle,
  Konfidenz, wann zuletzt gesagt, Art, Sitzung und Thema (ein Klick zeigt nur deren Fakten), was er
  ersetzt hat und wodurch er ersetzt wurde (beides anklickbar) und **wo es gesagt wurde** — die passendsten Stellen der Sitzungen um diesen Zeitpunkt,
  wörtlich, Zugangsdaten geschwärzt. **Esc** schließt;
- **Pflege** (oben, aufklappbar; die Kopfzeile zählt, was wartet) — was einen Blick braucht, an einer Stelle:
  - **Zur Freigabe** — mit `approve_writes = true` unter `[memory]` legt der Agent seine Schreibvorgänge
    (`wiki_remember`) nur vor: was er hinzufügen (+) und was er schließen (−) will. **Freigeben** lässt den
    Vorgang ins Gedächtnis (gültig ab dem Zeitpunkt, an dem der Agent ihn vorgelegt hat), **Verwerfen** legt
    ihn in ein Prüfprotokoll. Auf der Kommandozeile: `openwiki memory pending` / `approve` / `reject`;
  - **Zu prüfen** — die aktuellen Fakten der Arten, die von selbst veralten (Pläne, Zahlen, Lücken, Versionen,
    laufende Zustände), die wahrscheinlich veralteten Arten zuerst, darin die am längsten nicht bestätigten.
    **stimmt noch** merkt den Fakt als neu bestätigt (er rückt ans Ende), **nicht mehr wahr** beendet seine
    Gültigkeit jetzt — er bleibt als Geschichte erhalten;
  - **Zum Vergessen** — was der Schlaf-Durchlauf (`openwiki sleep`) vergessen würde: einmalige
    Sitzungsereignisse und Fakten gegen die Sicherheitsregel. **vergessen** archiviert (nichts wird gelöscht).

  Jede Aktion geht über das Journal und startet eine Faltung; die Zeile zeigt „eingereiht", bis sie nach
  wenigen Sekunden angewendet ist. Mit `--dry-run` oder im Wiki-Modus ist die Pflege nur lesend.

**Zeit im Gedächtnis (bitemporal).** Jeder Fakt kennt zwei Zeiten: *wann er in der Welt
galt* (z. B. „seit 2025-09-16" — aus einem im Gespräch genannten Datum, sonst dem
Sitzungsdatum) und *wann OpenWiki ihn erfasst hat*. Deshalb landet eine nachträglich
erfasste ältere Sitzung in der Vergangenheit, statt den aktuellen Stand zu überschreiben.
Status: **überholt** = die Welt hat sich geändert (Intervall beendet); **zurückgezogen** =
war nie wahr (Korrektur, `remember --correct`); **geplant** = gilt erst ab einem künftigen
Datum; **vergessen** = vom nächtlichen Schlaf-Durchlauf (`openwiki sleep`) archiviert — ein
einmaliges Sitzungsereignis wie „v0.74.0 wurde gepusht und getaggt" (nicht gelöscht, nur nicht
mehr abgerufen).

**Mitnehmen und nachlesen.** `openwiki memory export` schreibt das Gedächtnis als COGX-Archiv —
das Austauschformat von Cognee, das auch andere Gedächtnis-Systeme lesen (`--full` = verlustfreie
Sicherung samt Embeddings) —, `openwiki memory import` stellt es wieder her. Der Schlaf-Durchlauf
schreibt außerdem eine lesbare Markdown-Ansicht nach `memory/` im Projekt (eine Datei pro Subjekt
mit aktuellem Stand und Verlauf), die sich mit git verfolgen lässt.

**Hybrider Abruf.** Neben der Bedeutungsähnlichkeit (Embedding) holt ein Stichwortabgleich (BM25) Fakten mit genau
den Namen, Versionen oder Dateinamen der Anfrage herein — er tauscht nur unter den ähnlichsten Fakten aus, die
Reihenfolge bleibt die der Ähnlichkeit (`[memory] lexical_weight`, Standard 0.2; `0` schaltet ihn ab). Nennt eine
Frage einen Zeitraum („im Juli 2023", „letzte Woche"), werden Fakten aus diesem Zeitraum bevorzugt
(`[memory] temporal_weight`, Standard 0.1).

Nur lesend; ohne Sitzungen (oder im Wiki-Modus) zeigt der Reiter einen Hinweis.

## Evaluation (Reiter „Evaluation")

Der Reiter **Evaluation** führt den Retrieval-Benchmark des Projekts (`eval.jsonl`)
live aus und vergleicht **RAG** gegen **GraphRAG** (Kennzahlen­tabelle mit Schiebereglern
für `top_k`/`expand_k`, Fehl-Analyse). Dazu ein **Live-A/B** (eine Frage durch beide
Retriever nebeneinander), eine **Antwortqualität**-Auswertung (Zitat-Treffer + LLM-Richter,
als Hintergrundjob) und ein **KB-Health**-Panel (Konnektivität, verwaiste Seiten,
Begriffs-Hubs). Alles nur lesend.

## System (Reiter „System")

Der Reiter **System** ist die **Beobachtbarkeit** (Observability): Jeder Chat-,
Einbettungs- und API-Aufruf wird mit **Latenz** und **Token-Zahl** erfasst (was Ollama
zurückmeldet). Angezeigt werden Zusammenfassungskarten je Art (Aufrufe, p50/p95,
Gesamtzeit, Tokens) und eine Liste der letzten Ereignisse, die sich alle 2 s
aktualisiert. Dieselben Zahlen erscheinen auch im CLI (`⏱`-Fußzeile bei `ask`) und je
Build-Stufe im Reiter **Projekt**.

## Ein neues Wiki anlegen

**Als Projekt (empfohlen).** Ein *Projekt* bündelt ein Wiki in einem Ordner mit der
Manifest-Datei `openwiki.toml` und eigenen Ausgaben — so bleibt der Zustand erhalten
und Sie können mehrere Wikis nebeneinander pflegen und dazwischen wechseln:

```bash
openwiki init mein-handbuch --source mein-handbuch.pdf   # legt openwiki.toml + sources/ an
cd mein-handbuch
openwiki build                                           # ingest → wiki → index → graph, laut Manifest
openwiki status                                          # Quellen, Einstellungen, Build-Status je Stufe
openwiki serve --port 8137                               # bedient DIESES Projekt (Reiter „Projekt")
```

`openwiki build` läuft **inkrementell**: eine Prüfsumme je Stufe in
`.openwiki/state.json` überspringt Stufen, deren Eingaben und Einstellungen
unverändert sind (`--only ingest,wiki,index,graph`, `--force`). Alle Einstellungen
(Modelle, `split_level`, Chunk-Größe, Entitäten …) stehen im `openwiki.toml`; ändern
Sie es und führen Sie `openwiki build` erneut aus. Mehrere `[[sources]]`
verschmelzen zu *einem* Korpus — weitere Quelle hinzufügen mit
`openwiki project add-source weitere.pdf`. Projektübergreifende Vorgaben (z. B. Ihr
Ollama-Host) gehören nach `~/.openwiki/config.toml`.

**Einzelschritte (ohne Projekt).** Dieselbe Pipeline von Hand; jeder Befehl schreibt
nach `output/` und baut auf dem vorigen auf (Datei z. B. `mein-handbuch.pdf`):

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
# volle Tiefe: getypte Beziehungen + kanonische Entitäten (Aliasse):
openwiki graph-build output/mein-handbuch.json --relations --resolve-entities
# → output/graph/
# optional: Themen/Communities berechnen (für Themenfarben + globale Suche):
openwiki communities
```

Im **Projekt-Modus** genügt es, im `openwiki.toml` unter `[graph]` die Schalter
`entities`/`relations`/`resolve_entities` zu setzen und `openwiki build` erneut
auszuführen — der Gedächtnis-Tier bleibt dabei erhalten.

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
- Auf der Kommandozeile bietet `openwiki ask` messbare Varianten: `--hybrid`
  (BM25 + Vektor), `--rerank` (LLM-Neusortierung) und `--global` (thematische
  Antwort aus den Communities). Ob sie sich lohnen, zeigt `openwiki eval`.

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
