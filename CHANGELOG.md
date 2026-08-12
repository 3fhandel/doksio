# Doksio Änderungsprotokoll

## Build {{ build_number }}

**Datum/Uhrzeit:** {{ build_datetime }}

### Neuerungen

- Die Buildnummer in der Kopfzeile öffnet jetzt dieses Änderungsprotokoll.
- Dokumente können aus PDF- und Bilddateien zu einem neuen PDF zusammengeführt werden.
- Beim Zusammenführen ist die Box des Ausgangsdokuments vorausgewählt und die Treffer lassen sich sortieren.
- Importquellen können mehrseitige Stapelscan-PDFs automatisch seitenweise als einzelne Dokumente importieren.

### Änderungen

- Die Dokumentverwaltung zeigt Aufteilen, Zusammenführen und Löschen in einer ruhigen, einheitlich beschrifteten Werkzeugleiste.
- Beim Zusammenführen werden Quelldokumente standardmäßig erst nach erfolgreicher Erstellung des neuen Dokuments gelöscht.
- Die Buildnummer wird in lokalen und produktiven Containern automatisch aus den Buildmetadaten ermittelt.
- Änderungen am Änderungsprotokoll werden im lokalen Testbetrieb ohne Neustart sichtbar.
- Die normale Volltextsuche nutzt den PostgreSQL-Suchindex ohne zusätzliche langsame Teiltextprüfung und überträgt weniger interne Indexdaten; Teilwörter bleiben über den gleichnamigen Schalter verfügbar.

## Build 20260806-1453

**Datum/Uhrzeit:** 06.08.2026, 14:53 Uhr

### Neuerungen

- Workflow-Supervisoren erhalten eine eigene Übersicht mit Backlog und allen beaufsichtigten Aufgaben.
- Wiedervorlagen stehen unabhängig von der Dokumentenbox zur Verfügung.

### Änderungen

- Die Suche stellt Suchbegriff, Dokumentenbox und Tags übersichtlicher und platzsparender dar.
- Die Navigation durch Ergebnislisten bleibt auch nach dem Bearbeiten eines Dokuments erhalten.
- Die Anmeldung am Mandanteneinstieg reagiert bei unvollständigen OIDC-Zuordnungen kontrolliert statt mit einem Serverfehler.
- Für erfolgreiche und nicht importierbare E-Mails können einheitlich Löschen, Verschieben oder keine Aktion gewählt werden.
- Die Oberfläche zum Aufteilen von Dokumenten zeigt Seiten, Titel und Zielangaben klarer an.

## Build 20260805-1020

**Datum/Uhrzeit:** 05.08.2026, 10:20 Uhr

### Neuerungen

- Auch gescannte Dokumente lassen sich innerhalb der Dokumentvorschau durchsuchen.
- Für Workflows können eine oder mehrere Supervisor-Rollen festgelegt werden.
- Öffentliche Rollen können in Kommentaren erwähnt werden; die Mitglieder werden gemeinsam benachrichtigt.
- Beim Löschen eines Metadatenfelds können vorhandene Werte in ein kompatibles Feld übernommen werden.

### Änderungen

- Dokumentenboxen, Benutzer und Rollen werden über einheitliche, durchsuchbare Auswahlfelder gewählt, die auch per Tastatur bedienbar sind.
- Workflows können mehreren Dokumentenboxen zugeordnet werden; bei manuellen Workflows begrenzt diese Auswahl den möglichen Start.
- Für Metadatenfelder lässt sich festlegen, ob sie an Kindboxen vererbt werden.
- Rollenverwaltung und Dokumentverknüpfung wurden übersichtlicher und kompakter gestaltet.

## Build 20260804-1017

**Datum/Uhrzeit:** 04.08.2026, 10:17 Uhr

### Neuerungen

- Die Dokumenttoolbar bietet eine Suche mit Trefferanzahl, Seitensprung und Navigation durch Texttreffer in PDFs.
- Suchbegriffe aus der allgemeinen Suche werden beim Öffnen eines Dokuments direkt übernommen.

### Änderungen

- Kopfbereich und Aktionen der Dokumentdetailansicht sind kompakter und klarer gegliedert.
- `Strg+F` beziehungsweise `Cmd+F` fokussiert die Suche im geöffneten Dokument.

## Build 20260803-1041

**Datum/Uhrzeit:** 03.08.2026, 10:41 Uhr

### Neuerungen

- Treffer aus dem Volltext öffnen ein Dokument direkt auf der passenden Seite.

### Änderungen

- Suchergebnisse übergeben den Suchkontext an die Dokumentansicht, damit die Fundstelle leichter nachvollziehbar ist.

## Build 20260731-0659

**Datum/Uhrzeit:** 31.07.2026, 06:59 Uhr

### Neuerungen

- Auswahllisten für Metadaten werden zentral mit stabilen Einträgen, Anzeigenamen, Sortierung und Aktivstatus verwaltet.
- Eine Auswahlliste kann in mehreren Dokumentenboxen wiederverwendet werden.

### Änderungen

- Lange Auswahllisten lassen sich beim Bearbeiten eines Dokuments direkt im Auswahlfeld durchsuchen.
- Nicht mehr benötigte Auswahlwerte können deaktiviert werden, ohne bestehende Dokumentwerte zu verlieren.

## Build 20260730-2135

**Datum/Uhrzeit:** 30.07.2026, 21:35 Uhr

### Neuerungen

- Berechtigte Posteingänge nehmen noch nicht zugeordnete Dokumente auf und unterstützen deren gemeinsame Sichtung und Verteilung.
- Wiedervorlagen erinnern Benutzer zu einem frei wählbaren Datum mit einer persönlichen Bemerkung an ein Dokument.

### Änderungen

- Die Stapelverarbeitung wurde als übersichtlicher Zuordnungsarbeitsplatz für Posteingänge weiterentwickelt.
- Zugriffe auf Posteingänge und erlaubte Zielboxen lassen sich getrennt über Rollen steuern.

## Build 20260729-2209

**Datum/Uhrzeit:** 29.07.2026, 22:09 Uhr

### Neuerungen

- Die optionale Teilwortsuche findet nummernartige Begriffe auch innerhalb längerer Zeichenfolgen.
- Die Erweiterte Prüfhilfe erlaubt gemeinsame, persistente Prüfzeichen direkt auf Dokumentseiten.
- Ein integrierter Hilfebereich erklärt zentrale Arbeitsabläufe in verständlicher Sprache.
- Wer in einem Dokument erwähnt wurde, wird auch über nachfolgende Kommentare informiert.

### Änderungen

- Die Suche und die Navigation durch große Ergebnislisten wurden beschleunigt.
- Die Dokumentansicht und ihre Prüfhilfen wurden kompakter und besser bedienbar gestaltet.

## Build 20260727-2322

**Datum/Uhrzeit:** 27.07.2026, 23:22 Uhr

### Neuerungen

- Der persönliche Verlauf zeigt die zuletzt geöffneten Dokumente und ist standardmäßig über `Alt+H` erreichbar.
- Mehrseitige PDFs können vollständig durchgescrollt und seitenweise gedreht werden.
- DOCX- und ODT-Dateien können importiert, verarbeitet und in der Vorschau dargestellt werden.
- Workflow-Schritte können optional eine zusätzliche Bestätigung verlangen.

### Änderungen

- Vorheriges und nächstes Dokument navigieren durch die gesamte Ursprungsliste statt nur durch die aktuelle Seite.
- Stapelimporte laden Vorschauen dynamisch nach und speichern Zuordnungen unmittelbar.
- Der E-Mail-Import berücksichtigt erlaubte MIME-Typen und nimmt eine Nachricht nur bei mindestens einem importierbaren Anhang an.
- Dokumentlisten, Suche und Detailansicht reagieren bei großen Beständen schneller; die Seitenrenderzeit wird ausgewiesen.

## Build 20260726-2355

**Datum/Uhrzeit:** 26.07.2026, 23:55 Uhr

### Neuerungen

- Benutzer können beliebig viele Alarme aus Suchfiltern anlegen und sich bei passenden neuen Dokumenten in Doksio oder per E-Mail benachrichtigen lassen.
- Eine zentrale Hintergrundjob-Verwaltung zeigt wartende und laufende Aufgaben und erlaubt deren Abbruch.

### Änderungen

- Audit-Einträge lassen sich nach Zeitraum und Ereignistyp filtern.
- Titelregeln können mehreren Dokumentenboxen zugeordnet werden.
- Die PDF-Vorschau unterstützt mehrere Seiten bei weiterhin seitenbezogener Drehung.

## Build 20260724-0706

**Datum/Uhrzeit:** 24.07.2026, 07:06 Uhr

### Neuerungen

- Zentrale Regeln bestimmen Dokumenttitel einheitlich je Dokumentenbox.
- Titel können aus E-Rechnungsdaten mit Formatvorlage und einer Fallback-Strategie erzeugt werden.
- Eine Wartung kann die Titel einer ganzen Dokumentenbox nach den aktuell gültigen Regeln neu setzen.
- Eine auf Rechnungen spezialisierte OCR-Titelfindung erkennt Absender, Rechnungsnummer und Belegdatum zuverlässiger.

### Änderungen

- Unterbrochene Wartungsaufträge lassen sich fortsetzen und melden Probleme mit Worker oder Warteschlange verständlich zurück.
- Die automatische Titelfindung vermeidet allgemeine Textzeilen und den eigenen Firmenanschriftblock besser.

## Build 20260723-2322

**Datum/Uhrzeit:** 23.07.2026, 23:22 Uhr

### Neuerungen

- Datumsfelder verstehen kurze Eingaben wie `23`, `2307`, relative Angaben und Begriffe wie `heute`.
- Länger laufende Wartungen werden stapelweise ausgeführt und können ihren Fortschritt anzeigen.
- Aus Doksio versendete Benachrichtigungen und Auto-Antworten verwenden ein einheitliches HTML-Maildesign.
- Dokumentlisten zeigen an, ob durchsuchbarer Volltext vorhanden ist; danach kann auch gefiltert werden.

### Änderungen

- Hintergrundaufgaben und OCR-Status werden zuverlässiger erfasst.
- Die zentrale Titelfindung wurde als Grundlage für einheitliche Importtitel eingeführt.

## Build 20260721-1021

**Datum/Uhrzeit:** 21.07.2026, 10:21 Uhr

### Neuerungen

- Mandantenadministratoren können die hinterlegten SMTP-Einstellungen durch eine Testmail prüfen.
- Der Workflow-Schritt `Daten vervollständigen` verlangt ausgewählte Metadaten und erledigt sich bei bereits vorhandenen Werten automatisch.

### Änderungen

- Komplexe Workflows werden platzsparender dargestellt und brechen nachvollziehbar in mehrere Zeilen um.
- Verbindungspfeile und die direkte Aktualisierung der Workflow-Visualisierung wurden verbessert.

## Build 20260720-2141

**Datum/Uhrzeit:** 20.07.2026, 21:41 Uhr

### Neuerungen

- OIDC-Anmeldung kann Benutzer über E-Mail und Mandanten-Claims einem vorhandenen Doksio-Konto zuordnen.
- Dokumentenboxen können nach einer ausdrücklichen Namensbestätigung vollständig und dauerhaft geleert werden.
- Ein eigener Stapelimport mit Berechtigung, Vorschau und nachträglicher Zuordnung verarbeitet viele Dokumente gemeinsam.
- Benachrichtigungsarten lassen sich getrennt für In-App- und E-Mail-Zustellung konfigurieren.
- Dokumentseiten können persistent gedreht werden.

### Änderungen

- Für reine OIDC-Konten können Administratoren Benutzer ohne lokales Passwort anlegen.
- Das persönliche Profil weist auf die Verwaltung durch den Identity Provider hin und schützt übernommene Kontodaten vor lokaler Änderung.
- Die Dokumentdetailansicht wurde neu geordnet und für die tägliche Bearbeitung verdichtet.

## Build 20260714-1235

**Datum/Uhrzeit:** 14.07.2026, 12:35 Uhr

### Neuerungen

- Doksio unterstützt die Anmeldung über einen OIDC-kompatiblen Identity Provider wie Authentik.
- Benutzerprofile bieten Einstellungen für Account, Benachrichtigungen und Tastenkürzel.
- Das Doksio-Logo und ein passendes Favicon wurden in die Anwendung eingebunden.

### Änderungen

- Anzeigename, E-Mail-Adresse und Benachrichtigungseinstellungen können von Administratoren vollständig gepflegt werden.
- Erwähnungen und Workflow-Ereignisse wurden als getrennte Benachrichtigungstypen ausgebaut.

## Build 20260713-1550

**Datum/Uhrzeit:** 13.07.2026, 15:50 Uhr

### Neuerungen

- Ein produktionsnaher Docker-Stack mit PostgreSQL, Redis, MinIO, Webanwendung und Worker kann über Portainer betrieben werden.
- Dokumentenboxen können gelöscht werden; enthaltene Dokumente lassen sich dabei verschieben oder löschen.
- Ordnerimporte stehen als Skripte für Linux, macOS und Windows mit Protokollierung und Dublettenbehandlung bereit.
- TIFF-Dokumente werden für Vorschau und OCR verarbeitet.
- Dokumente können per Link, E-Mail und Systemdialog geteilt werden.
- Mandantenspezifische IMAP-Postfächer importieren geeignete Mailanhänge automatisch.

### Änderungen

- Gunicorn und der Stack lauschen auf den für Containerbetrieb vorgesehenen Schnittstellen.
- OCR-Aufträge werden kontrolliert über Hintergrundworker verarbeitet.
- Buildinformationen werden automatisch aus dem Projektstand angezeigt.

## Build 20260707-0054

**Datum/Uhrzeit:** 07.07.2026, 00:54 Uhr

### Neuerungen

- Die erste lauffähige Doksio-Webanwendung verwaltet Mandanten, Benutzer, Rollen und hierarchische Dokumentenboxen.
- Dokumente werden unverändert abgelegt, lokal per OCR erfasst und über Volltext und Filter gesucht.
- Kommentare, Tags, boxabhängige Metadaten und generische Workflows stehen als Grundfunktionen bereit.
- System- und Mandantenbereiche besitzen getrennte Anmeldungen und eindeutig zugeordnete URL-Pfade.
- Ein responsives Django- und HTMX-Frontend mit Dokumentvorschau bildet die Grundlage der Bedienoberfläche.

### Änderungen

- Das Projekt wurde modular in eigenständige Bereiche für Dokumente, Benutzer, Audit, OCR, Suche, Speicher und Workflows gegliedert.
- Docker-Konfiguration, automatisierte Tests und eine Demo-Umgebung wurden ergänzt.

## Build 20260706-1714

**Datum/Uhrzeit:** 06.07.2026, 17:14 Uhr

### Neuerungen

- Vision, fachlicher Umfang und Zielgruppe von Doksio wurden erstmals festgehalten.
- Die Grundarchitektur für Mandantenfähigkeit, unveränderbare Dokumentablage, Workflows, Exporte und das serverseitige Frontend wurde beschlossen.

### Änderungen

- Architekturentscheidungen dokumentieren die Leitplanken für eine saubere, modulare und langfristig wartbare Umsetzung.
