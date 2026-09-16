# Tutorial

Eine kurze, geführte Tour durch OpenWiki. Jeder Schritt hat einen
**▶ Ausprobieren**-Knopf, der die Aktion direkt in der Oberfläche ausführt.

## 1. Eine Seite öffnen

Der Navigationsbaum links bildet die Kapitelstruktur des Handbuchs ab. Klicken
Sie auf einen Eintrag, um die Seite in der Mitte zu lesen.

[▶ Beispiel „Vorstellung des NAUTILUS“ öffnen](<run:page:003-vorstellung-des-nautilus>)

## 2. Semantisch suchen

Die Suche versteht **Bedeutung**, nicht nur Stichwörter — formulieren Sie ruhig
eine ganze Frage. Die Treffer erscheinen links, jeweils mit Ähnlichkeitswert und
PDF-Seiten; ein Klick öffnet die Seite.

[▶ Suche „Lautstärke einstellen“ ausführen](<run:search:Lautstärke einstellen>)

## 3. Den Agenten fragen

Stellen Sie rechts eine Frage in natürlicher Sprache. Der Agent sucht, liest die
passenden Seiten und antwortet mit **Quellenangaben** — nur auf Basis des
Handbuchs. Unter der Antwort sehen Sie die Werkzeug-Chips (z. B. `· search_wiki`).

[▶ Frage stellen: „Was ist Smooth Sound Transitions?“](<run:ask:Was ist Smooth Sound Transitions (SST)? Antworte in einem Satz.>)

## 4. Eine Seite bearbeiten lassen

Der Agent kann das Wiki auch **verändern**. Bitten Sie ihn z. B., eine neue Seite
anzulegen — das ist unkritisch, da keine bestehende Handbuchseite berührt wird.
Achten Sie auf den **✎**-Chip (schreibendes Werkzeug); die neue Seite erscheint
anschließend direkt in der Mitte.

[▶ Neue Seite „notizen“ anlegen lassen](<run:ask:Erstelle eine neue Wiki-Seite mit dem Slug 'notizen' und dem Titel 'Meine Notizen' mit einem kurzen Willkommenssatz.>)

## 5. Beziehungen im Graph erkunden

Der Reiter **Graph** ist ein interaktiver Explorer rund um die aktuelle Seite:
**Seiten** (Kreise) und **Begriffe** (Rauten) mit farbig kodierten Beziehungen
(Hierarchie, Lesereihenfolge, ähnliche Seiten, „siehe Seite N"-Querverweise,
gemeinsame Begriffe und — falls mit `--relations` gebaut — getypte **Beziehungen**).
**Klicken Sie einen Knoten an, um ihn zu erweitern** (und **doppelklicken**, um ihn
wieder einzuklappen) und so das Netz auf- und abzubauen, **ziehen** Sie Knoten zum
Anordnen, blenden Sie über die **Legenden-Chips** Kantenarten aus, und öffnen Sie
eine Seite mit **„Seite öffnen →"**. Sind Themen berechnet, färbt „Themenfarben" die
Seiten nach Community ein.

[▶ Graph für „Smooth Sound Transitions“ öffnen](<run:graph:025-smooth-sound-transitions-sst>)

## 6. Das Projekt im Blick

Wurde der Server in einem **Projekt** gestartet (einem Ordner mit `openwiki.toml`),
zeigt der Reiter **Projekt** dessen Zustand: die **Quellen**, den **Build-Status**
je Stufe (`ingest`, `wiki`, `index`, `graph` — *aktuell*, *veraltet* oder *fehlt*)
und die verwendeten Modelle. Sind **Themen (Communities)** berechnet, beantwortet das
**Globale-Suche**-Feld dort eine *thematische* Frage („Wie hängen die Hauptthemen
zusammen?") aus den Zusammenfassungen — was die reine Abschnitts-Suche nicht kann. Ein
eigenes Projekt legen Sie mit `openwiki init` an — Details im Reiter **Hilfe**.

[▶ Reiter „Projekt" öffnen](<run:tab:project>)

## 7. Die Struktur vermessen (Analyse)

Der Reiter **Analyse** vermisst, *wie gut organisiert* das Wissen ist: Wo stimmen
**Graph** und **semantischer Raum** überein (redundant), und wo fügt der Graph Struktur
hinzu, die reine Ähnlichkeit nicht sieht (die **Graph-Reichweite**)? Dazu eine
**semantische Karte** — die Seiten in 2D, nach Thema eingefärbt, mit überlagerten
Graphkanten. Nur lesend, ohne Ollama-Aufruf.

[▶ Reiter „Analyse" öffnen](<run:tab:analyse>)

## 8. Das Gedächtnis (Second Brain)

Im **Second-Brain-Modus** führt der Graph einen **Gedächtnis-Tier**: aus Sitzungen
*erinnerte* Fakten, die später *abgerufen* werden — mit Widerspruchs­behandlung und
Konsolidierung zu Themen. Der Reiter **Gedächtnis** zeigt Identität, Kennzahlen,
Themen-Karten und eine Faktentabelle (leer, solange nichts erinnert wurde).

[▶ Reiter „Gedächtnis" öffnen](<run:tab:memory>)

## 9. Unter die Haube schauen (System)

Der Reiter **System** ist die **Beobachtbarkeit**: Jeder Chat-, Einbettungs- und
API-Aufruf wird mit **Latenz** und **Token-Zahl** erfasst und alle 2 s aktualisiert —
stellen Sie eine Frage und beobachten Sie die Zahlen live.

[▶ Reiter „System" öffnen](<run:tab:system>)

## 10. Weiter geht's

- Kombinieren Sie Suche und Chat: erst ein Thema finden, dann gezielt nachfragen.
- Bitten Sie den Agenten, einen Abschnitt zusammenzufassen und als neuen Abschnitt
  an eine Seite anzuhängen.
- Wandern Sie im **Graph** von einer Seite zu ihren Querverweisen (und getypten
  **Beziehungen**) und zurück.
- Vergleichen Sie im Reiter **Evaluation** RAG gegen GraphRAG auf dem Benchmark des
  Projekts.
- Verwalten Sie mehrere Wikis als **Projekte** (`openwiki init` → `openwiki build`)
  und wechseln Sie dazwischen — Grundlagen im Reiter **Hilfe**.
- Alle Funktionen im Detail finden Sie im Reiter **Hilfe**.
