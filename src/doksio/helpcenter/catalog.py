from __future__ import annotations

from dataclasses import dataclass

from doksio.accounts.permissions import TenantPermissions
from doksio.documents.policies import can_administer_tenant, has_tenant_permission


@dataclass(frozen=True)
class HelpSection:
    title: str
    text: str
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpTopic:
    slug: str
    title: str
    summary: str
    icon: str
    sections: tuple[HelpSection, ...]
    quick_tips: tuple[str, ...] = ()
    permission_code: str = ""
    admin_only: bool = False


HELP_TOPICS = (
    HelpTopic(
        slug="erste-schritte",
        title="Erste Schritte",
        summary="Die wichtigsten Bereiche und ein guter Einstieg in Doksio.",
        icon="home",
        sections=(
            HelpSection(
                title="Dein Arbeitsplatz",
                text=(
                    "Das Dashboard zeigt neue Dokumente und deine offenen Aufgaben. "
                    "Über das linke Menü wechselst du zwischen Ablage, Suche und "
                    "deinen Arbeitsvorgängen."
                ),
            ),
            HelpSection(
                title="Ein typischer Ablauf",
                text="So bearbeitest du einen neuen Vorgang:",
                steps=(
                    "Öffne eine Aufgabe auf dem Dashboard oder unter „Meine Aufgaben“.",
                    "Prüfe Dokument, Kerndaten, Kommentare und verknüpfte Dokumente.",
                    "Erledige den hervorgehobenen Workflow-Schritt.",
                ),
            ),
        ),
        quick_tips=(
            "Deine offenen Aufgaben erkennst du am gelben Zähler im linken Menü.",
            "Über deinen Namen erreichst du Profil, Benachrichtigungen und Verlauf.",
        ),
    ),
    HelpTopic(
        slug="dokumente-finden",
        title="Dokumente finden",
        summary="Dokumentenboxen durchsuchen und gezielt mit Filtern arbeiten.",
        icon="search",
        sections=(
            HelpSection(
                title="Durch Dokumentenboxen navigieren",
                text=(
                    "Unter „Dokumente“ öffnest du Boxen wie Verzeichnisse. „..“ führt "
                    "eine Ebene zurück. Unterboxen stehen immer oberhalb der Belege."
                ),
            ),
            HelpSection(
                title="Die Suche eingrenzen",
                text="Kombiniere nur die Filter, die du wirklich benötigst:",
                steps=(
                    "Gib einen Begriff, eine Nummer oder einen Namen ein.",
                    (
                        "Wähle die Dokumentenbox direkt neben dem Suchbegriff. "
                        "Unterboxen kannst du dort ebenfalls einbeziehen."
                    ),
                    (
                        "Weitere Filter wie Zeitraum, Tags oder Workflow-Status "
                        "findest du im aufklappbaren Filterbereich. Tags kannst du "
                        "dort durchsuchen und einzeln oder gemeinsam auswählen."
                    ),
                    (
                        "Bei Auswahllisten-Metadaten kannst du direkt in das Feld "
                        "tippen und anschließend einen passenden Eintrag wählen."
                    ),
                    (
                        "Aktiviere „Teilwörter finden“ nur für Suchen innerhalb "
                        "längerer Werte."
                    ),
                ),
            ),
            HelpSection(
                title="Fundstellen im Volltext öffnen",
                text=(
                    "Wenn ein Suchbegriff auf einer bestimmten Dokumentseite "
                    "gefunden wurde, zeigt Doksio die Seitenzahl am Treffer. "
                    "Beim Öffnen springt die Vorschau direkt zu dieser Seite."
                ),
            ),
        ),
        quick_tips=(
            "Die Suche neben „Hochladen“ übernimmt die aktuell geöffnete Box.",
            "Ein Klick auf das Vorschaubild öffnet eine schnelle Dokumentvorschau.",
        ),
    ),
    HelpTopic(
        slug="dokumente-hochladen",
        title="Dokumente hochladen",
        summary="Einzelne oder mehrere Dateien sicher in Doksio ablegen.",
        icon="upload",
        sections=(
            HelpSection(
                title="Dateien hochladen",
                text=(
                    "Dateien können ausgewählt oder per Drag-and-drop "
                    "hinzugefügt werden."
                ),
                steps=(
                    (
                        "Wähle die passende Dokumentenbox oder nutze die "
                        "automatische Zuordnung."
                    ),
                    "Füge eine oder mehrere Dateien hinzu.",
                    "Lass den Titel leer, wenn Doksio ihn automatisch bestimmen soll.",
                    "Starte den Upload und warte auf die Bestätigung.",
                ),
            ),
            HelpSection(
                title="Verarbeitung im Hintergrund",
                text=(
                    "Nach dem Speichern laufen je nach Dateityp "
                    "Vorschauerstellung, OCR, "
                    "eRechnungs-Auswertung und passende Workflows automatisch."
                ),
            ),
        ),
        quick_tips=(
            "Dubletten werden erkannt und nicht erneut abgelegt.",
            (
                "Mehrere Dateien erhalten jeweils einen eigenen automatisch "
                "ermittelten Titel."
            ),
        ),
    ),
    HelpTopic(
        slug="dokument-bearbeiten",
        title="Mit Dokumenten arbeiten",
        summary="Vorschau, Kerndaten, Kommentare und Prüfhilfen verwenden.",
        icon="file",
        sections=(
            HelpSection(
                title="Die Detailansicht",
                text=(
                    "Links stehen Arbeitsinformationen und Aktionen, rechts die "
                    "Vorschau. Volltext und eRechnungs-Daten lassen sich unter "
                    "der Vorschau öffnen. In der PDF-Toolbar kannst du das "
                    "aktuelle Dokument durchsuchen und zwischen Fundstellen "
                    "wechseln. Bei PDFs mit eingebettetem Text und bei Scans mit "
                    "vorhandenen OCR-Suchmarkierungen werden Treffer direkt auf "
                    "der Seite hervorgehoben. Öffnest du ein Dokument "
                    "aus der Volltextsuche, wird der gefundene Begriff automatisch "
                    "in die Dokumentensuche übernommen. Mit Strg+F beziehungsweise "
                    "Cmd+F auf dem Mac springst du direkt in das Suchfeld. Im "
                    "Dokumentkopf findest du Wiedervorlage und Teilen; die Leiste "
                    "darunter wechselt zwischen Dokumenten der geöffneten Liste. "
                    "Wenn eine Bearbeitung das aktuelle Dokument aus dieser Liste "
                    "entfernt, etwa nach dem Erledigen einer Aufgabe, kannst du mit "
                    "Vorheriges und Nächstes in der verbleibenden Liste weiterarbeiten."
                ),
            ),
            HelpSection(
                title="Zusammenarbeiten",
                text=(
                    "Kommentare bleiben am Dokument erhalten. Mit @Benutzername "
                    "erwähnst "
                    "du Kollegen gezielt und löst deren Benachrichtigung aus."
                ),
            ),
            HelpSection(
                title="Dokumente aufteilen und zusammenführen",
                text=(
                    "Unter Verwaltung kannst du ein mehrseitiges PDF aufteilen "
                    "oder PDF- und Bilddokumente zusammenführen. Bilder werden dabei "
                    "als einzelne Seiten in das neue PDF übernommen. Beim Zusammenführen "
                    "ist die Box des Ausgangsdokuments vorausgewählt. Du kannst "
                    "weitere Dokumente über Suche und Filter auswählen und die "
                    "Treffer sortieren; standardmäßig stehen die neuesten oben. Danach "
                    "kannst du alle Seiten per Drag-and-drop in die gewünschte "
                    "Reihenfolge bringen. Standardmäßig werden die Quelldokumente "
                    "nach erfolgreicher Zusammenführung gelöscht; bei Bedarf "
                    "kannst du sie stattdessen behalten."
                ),
            ),
            HelpSection(
                title="Dokumente verknüpfen",
                text=(
                    "Unter „Verknüpfte Dokumente“ öffnest du mit „Verknüpfung "
                    "hinzufügen“ einen Arbeitsbereich, der von links über die "
                    "Dokumentinformationen eingeblendet wird. "
                    "Auf größeren Bildschirmen bleibt die aktuelle Belegvorschau "
                    "rechts sichtbar und bedienbar, damit du zum Beispiel eine "
                    "Belegnummer ablesen kannst. Filtere die Treffer, wähle das "
                    "gewünschte Dokument aus und bestätige die Verknüpfung direkt "
                    "im Arbeitsbereich. Danach wird die Detailansicht mit der neuen "
                    "Verknüpfung geladen. Ein Klick auf ein Thumbnail öffnet zuvor "
                    "eine Schnellvorschau der Originaldatei. Beim Lösen einer bestehenden Verknüpfung "
                    "zeigt ein Bestätigungsdialog noch einmal das betroffene Dokument."
                ),
            ),
            HelpSection(
                title="Metadaten und Auswahllisten",
                text=(
                    "Bei langen Auswahllisten kannst du direkt in das Auswahlfeld "
                    "tippen und einen passenden Vorschlag wählen. Änderungen an der Optionsliste "
                    "ändern bereits gespeicherte Dokumentwerte nicht automatisch. "
                    "Ein entfernter Wert bleibt am Dokument als bisheriger Wert "
                    "sichtbar, bis du ihn bewusst durch einen aktuellen Eintrag ersetzt."
                ),
            ),
            HelpSection(
                title="Dokument aufteilen",
                text=(
                    "In der Seitenübersicht setzt du Trennungen zwischen den "
                    "gewünschten Abschnitten. Ein Klick auf ein Seitenthumbnail "
                    "öffnet die betreffende Seite als große Vorschau. Leere Titel "
                    "werden nach der Aufteilung mit der zentralen Titelfindung der "
                    "jeweiligen Zielbox ermittelt. Standardmäßig wird das "
                    "Originaldokument erst gelöscht, nachdem alle Teildokumente "
                    "erfolgreich erstellt wurden. Du kannst es bei Bedarf behalten."
                ),
            ),
            HelpSection(
                title="Prüfhilfen",
                text=(
                    "Die normale Prüfhilfe unterstützt beim zeilenweisen Lesen. "
                    "Wenn die "
                    "erweiterte Prüfhilfe für die Box aktiv ist, können zusätzlich "
                    "persistente Prüfsymbole auf einzelnen Seiten gesetzt werden."
                ),
            ),
            HelpSection(
                title="Wiedervorlagen",
                text=(
                    "Über die Schaltfläche Wiedervorlage am Dokument legst du eine "
                    "persönliche Erinnerung an. "
                    "Dort kannst du ein Datum und eine persönliche Bemerkung hinterlegen. "
                    "Am gewählten Tag benachrichtigt dich Doksio. Die Kanäle "
                    "In-App und E-Mail stellst du in deinem Profil ein. Unter "
                    "Profil > Wiedervorlagen findest du alle offenen persönlichen "
                    "Termine nach Datum sortiert."
                ),
            ),
        ),
        quick_tips=(
            "Ein Rechtsklick entfernt ein gesetztes Prüfsymbol.",
            "Kerndaten werden in einer eigenen kompakten Ansicht bearbeitet.",
            "Erledigte Wiedervorlagen kannst du direkt am Dokument abschließen.",
        ),
    ),
    HelpTopic(
        slug="aufgaben-workflows",
        title="Aufgaben und Workflows",
        summary="Offene Arbeit erkennen und Workflow-Schritte sicher abschließen.",
        icon="workflow",
        sections=(
            HelpSection(
                title="Was ist ein Workflow?",
                text=(
                    "Ein Workflow ist ein festgelegter Arbeitsablauf für ein "
                    "Dokument. Er sorgt dafür, dass notwendige Prüfungen und "
                    "Bearbeitungsschritte in der richtigen Reihenfolge erfolgen "
                    "und bei den zuständigen Personen ankommen."
                ),
                steps=(
                    (
                        "Ein neues Dokument startet automatisch den passenden "
                        "Workflow, zum Beispiel eine Rechnungsprüfung."
                    ),
                    (
                        "Der Workflow besteht aus einzelnen Schritten, etwa "
                        "Daten ergänzen, sachlich prüfen und freigeben."
                    ),
                    (
                        "Ein Schritt wird als Aufgabe bei den Benutzern "
                        "angezeigt, deren Rolle dafür zuständig ist."
                    ),
                    (
                        "Sobald alle Schritte abgeschlossen sind, ist auch der "
                        "Workflow beendet."
                    ),
                ),
            ),
            HelpSection(
                title="Workflow, Schritt und Aufgabe",
                text=(
                    "Der Workflow beschreibt den gesamten Ablauf. Ein "
                    "Workflow-Schritt ist eine einzelne Station darin. Unter "
                    "„Meine Aufgaben“ siehst du genau die aktuell offenen "
                    "Schritte, für die du verantwortlich bist."
                ),
            ),
            HelpSection(
                title="Passende Dokumentenboxen",
                text=(
                    "Ein Workflow kann auf bestimmte Dokumentenboxen begrenzt sein. "
                    "Ein manueller Workflow wird dir deshalb nur angeboten, wenn das "
                    "geöffnete Dokument in eine seiner ausgewählten Boxen gehört."
                ),
            ),
            HelpSection(
                title="Meine Aufgaben",
                text=(
                    "Hier erscheinen alle offenen Schritte, die dir über deine Rollen "
                    "zugeordnet sind. Bei mehreren Workflows kannst du die "
                    "Liste filtern. Wenn du für Workflows als Supervisor eingetragen "
                    "bist, führt die Schaltfläche „Supervisor-Übersicht“ zu deren "
                    "gesamtem Backlog. Dort siehst du offene Aufgaben, betroffene "
                    "Dokumente, aktive Durchläufe und die Erledigungen der letzten "
                    "sieben Tage. Supervisor-Aufgaben erscheinen nicht als deine "
                    "persönlichen Aufgaben, können dort aber von dir bearbeitet werden."
                ),
            ),
            HelpSection(
                title="Einen Schritt erledigen",
                text="Öffne das Dokument und prüfe die hervorgehobene Aufgabe:",
                steps=(
                    "Kontrolliere Dokument und vorhandene Informationen.",
                    "Ergänze erforderliche Metadaten oder Verknüpfungen.",
                    "Füge einen Kommentar hinzu, wenn der Schritt ihn verlangt.",
                    "Bestätige den Schritt über die hervorgehobene Aktion.",
                ),
            ),
        ),
        quick_tips=(
            (
                "Ein Workflow-Indikator zeigt, ob ein Dokument noch offene "
                "Schritte besitzt."
            ),
            "Abgeschlossene Schritte bleiben im Dokumenten-Log nachvollziehbar.",
        ),
    ),
    HelpTopic(
        slug="kommentare",
        title="Kommentare und Erwähnungen",
        summary="Kollegen einbeziehen und Gespräche am Dokument nachvollziehen.",
        icon="message-circle",
        sections=(
            HelpSection(
                title="Jemanden erwähnen",
                text=(
                    "Tippe im Kommentar @ und wähle einen Benutzer oder eine "
                    "öffentliche Gruppe aus der Vorschlagsliste. Einzelne Personen "
                    "beziehungsweise alle berechtigten Mitglieder der Gruppe erhalten "
                    "abhängig von ihrem Profil eine In-App- oder "
                    "E-Mail-Benachrichtigung."
                ),
            ),
            HelpSection(
                title="Im Gespräch bleiben",
                text=(
                    "Wer einmal an einem Dokument erwähnt wurde, wird auch über "
                    "spätere "
                    "Kommentare informiert. Eigene Kommentare lösen keine "
                    "Benachrichtigung an dich selbst aus."
                ),
            ),
        ),
        quick_tips=(
            "Kommentare verändern die Originaldatei nicht.",
            (
                "Die Benachrichtigungskanäle lassen sich im persönlichen "
                "Profil einstellen."
            ),
        ),
    ),
    HelpTopic(
        slug="stapelimport",
        title="Posteingänge und Stapel",
        summary=(
            "Eingegangene Dateien geschützt sichten, zuordnen und gemeinsam "
            "importieren."
        ),
        icon="inbox",
        permission_code=TenantPermissions.INBOXES_VIEW,
        sections=(
            HelpSection(
                title="Was ist ein Posteingang?",
                text=(
                    "Ein Posteingang ist eine vorläufige Ablage. Dateien sind dort "
                    "noch keine regulären Dokumente und werden erst durch die "
                    "Zuordnung zu einer Dokumentenbox endgültig importiert. "
                    "Zugriffsrollen trennen beispielsweise Personal- und "
                    "Buchhaltungsbelege."
                ),
            ),
            HelpSection(
                title="Einen Eingangsstapel bearbeiten",
                text=(
                    "Ein Stapel bleibt gespeichert, bis er abgeschlossen oder "
                    "verworfen "
                    "wird. Bereits getroffene Zuordnungen werden sofort gesichert."
                ),
                steps=(
                    "Lade alle zusammengehörigen Dateien in einen neuen Stapel.",
                    "Öffne den zuständigen Posteingang.",
                    "Prüfe die Vorschau und den Zuordnungsvorschlag jeder Datei.",
                    "Wähle die Zielbox oder markiere die Datei zum Überspringen.",
                    (
                        "Schließe den Stapel ab, sobald alle offenen Dateien "
                        "bearbeitet sind."
                    ),
                ),
            ),
        ),
        quick_tips=(
            (
                "Grün markierte Dateien sind zugeordnet, gelbe benötigen noch "
                "Aufmerksamkeit."
            ),
            "Office-Dateien erhalten nach dem Import eine PDF-Vorschau im Hintergrund.",
        ),
    ),
    HelpTopic(
        slug="tastenkuerzel",
        title="Tastenkürzel",
        summary="Häufige Bereiche und Aktionen schneller erreichen.",
        icon="keyboard",
        sections=(
            HelpSection(
                title="Eigene Kürzel",
                text=(
                    "Unter Profil → Tastenkürzel kannst du ein Feld auswählen und die "
                    "gewünschte Tastenkombination direkt drücken. Änderungen "
                    "gelten nur "
                    "für dein eigenes Benutzerkonto."
                ),
            ),
        ),
        quick_tips=(
            "Alt+H öffnet standardmäßig deinen Dokumentenverlauf.",
            "Bereits vergebene Kombinationen sollten nicht doppelt verwendet werden.",
        ),
    ),
    HelpTopic(
        slug="administration",
        title="Administration",
        summary="Benutzer, Rollen, Dokumentenboxen und Importe konfigurieren.",
        icon="settings",
        admin_only=True,
        sections=(
            HelpSection(
                title="Einstellungen strukturiert ändern",
                text=(
                    "Die Tenant-Einstellungen sind nach fachlichen Bereichen "
                    "gegliedert. "
                    "Änderungen an Rollen, Boxen und Workflows wirken auf die Arbeit "
                    "mehrerer Benutzer und sollten gezielt getestet werden."
                ),
            ),
            HelpSection(
                title="Mail-Import abschließen",
                text=(
                    "Für erfolgreich importierte und nicht importierbare Mails "
                    "wählst du jeweils eine eindeutige Aktion: in der Mailbox "
                    "belassen, als gelesen markieren, löschen oder in einen "
                    "Zielordner verschieben. Der Zielordner ist nur beim "
                    "Verschieben erforderlich."
                ),
            ),
            HelpSection(
                title="Berechtigungen",
                text=(
                    "Berechtigungen werden über mehrere Rollen additiv vergeben. "
                    "Dokumentenbox-Rechte beschränken zusätzlich, welche Ablagen und "
                    "Dokumente ein Benutzer sehen kann. Beim Erstellen einer Rolle "
                    "ist der Zugriff auf alle Dokumentenboxen nicht automatisch "
                    "aktiv. Rechte und Boxen können über die durchsuchbaren "
                    "Auswahllisten festgelegt werden. In einer bestehenden Rolle "
                    "siehst du außerdem die aktuell zugeordneten Benutzer."
                ),
            ),
            HelpSection(
                title="Workflow-Supervisor",
                text=(
                    "Für einen Workflow können eine oder mehrere Rollen als "
                    "Supervisor-Gruppen festgelegt werden. Deren Mitglieder dürfen "
                    "jeden offenen Schritt dieses Workflows bearbeiten. Die Aufgaben "
                    "erscheinen dadurch nicht unter „Meine Aufgaben“. Unter "
                    "„Auswertungen“ können Supervisor den betreffenden Workflow "
                    "auswählen und seine Kennzahlen einsehen."
                ),
            ),
            HelpSection(
                title="Öffentliche Gruppen",
                text=(
                    "Eine Rolle kann als öffentliche Gruppe markiert werden. Dann "
                    "erscheint sie bei @-Erwähnungen in Kommentaren. Eine Erwähnung "
                    "benachrichtigt alle aktiven Rollenmitglieder, die das Dokument "
                    "sehen dürfen."
                ),
            ),
            HelpSection(
                title="Einträge auswählen",
                text=(
                    "Dokumentenboxen und Rollen lassen sich in den Einstellungen "
                    "durchsuchen. Bei Mehrfachauswahlen kannst du die sichtbaren "
                    "Treffer gemeinsam auswählen oder abwählen. In kompakten Feldern "
                    "tippst du einen Teil des Namens oder Pfads ein und wählst den "
                    "passenden Treffer aus. Mit den Pfeiltasten wechselst du zwischen "
                    "Treffern, mit Enter übernimmst du den markierten Eintrag. Es "
                    "werden nur Einträge angeboten, die im jeweiligen Vorgang "
                    "verwendet werden dürfen."
                ),
            ),
            HelpSection(
                title="Zentrale Auswahllisten",
                text=(
                    "Unter Einstellungen > Auswahllisten pflegst du wiederverwendbare "
                    "Listen für Metadatenfelder. Eine Liste kann mehreren Feldern und "
                    "Dokumentenboxen zugeordnet werden. Einzelne Anzeigenamen lassen "
                    "sich ändern oder deaktivieren, ohne bestehende Dokumentzuordnungen "
                    "zu verlieren."
                ),
            ),
            HelpSection(
                title="Metadatenfelder in Kindboxen",
                text=(
                    "Beim Anlegen oder Bearbeiten eines Metadatenfelds legst du fest, "
                    "ob es an Kindboxen vererbt wird. Mit aktivierter Vererbung steht "
                    "das Feld in allen untergeordneten Dokumentenboxen zur Verfügung. "
                    "Ohne Vererbung gilt es ausschließlich in seiner eigenen Box."
                ),
            ),
            HelpSection(
                title="Metadatenfelder löschen",
                text=(
                    "Nicht mehr benötigte Felddefinitionen kannst du in der "
                    "Dokumentenbox löschen. Zur Sicherheit musst du den Feldnamen "
                    "bestätigen. Vorher kannst du vorhandene Werte in ein kompatibles "
                    "Feld desselben Typs übertragen, zum Beispiel in ein geerbtes "
                    "Feld. Bereits belegte Zielwerte werden dabei nicht überschrieben. "
                    "Ohne Übertragung bleiben die alten Werte technisch erhalten, "
                    "werden danach aber nicht mehr angezeigt oder bearbeitet. "
                    "Verwendet ein Workflow das Feld als Pflichtangabe, entfällt "
                    "diese Anforderung."
                ),
            ),
            HelpSection(
                title="OCR-Suchmarkierungen nachrüsten",
                text=(
                    "Unter Einstellungen > Wartung > OCR kannst du bestehende "
                    "Scan-PDFs für die Hervorhebung von Suchtreffern vorbereiten. "
                    "Die Verarbeitung läuft in kleinen Batches und kann nach einer "
                    "Unterbrechung fortgesetzt werden."
                ),
            ),
        ),
        quick_tips=(
            (
                "Status und Hintergrundjobs helfen bei der Kontrolle laufender "
                "Verarbeitung."
            ),
            (
                "Für Tests können eigene Boxen angelegt und später kontrolliert "
                "geleert werden."
            ),
        ),
    ),
)

TOPICS_BY_SLUG = {topic.slug: topic for topic in HELP_TOPICS}

CONTEXT_TOPIC_SLUGS = {
    ("documents", "dashboard"): "erste-schritte",
    ("documents", "tasks"): "aufgaben-workflows",
    ("documents", "supervisor_tasks"): "aufgaben-workflows",
    ("documents", "list"): "dokumente-finden",
    ("documents", "box"): "dokumente-finden",
    ("documents", "detail"): "dokument-bearbeiten",
    ("documents", "split"): "dokument-bearbeiten",
    ("documents", "merge"): "dokument-bearbeiten",
    ("documents", "core_metadata_edit"): "dokument-bearbeiten",
    ("documents", "upload"): "dokumente-hochladen",
    ("documents", "import_batch_list"): "stapelimport",
    ("documents", "inbox_list"): "stapelimport",
    ("documents", "inbox_detail"): "stapelimport",
    ("documents", "import_batch_upload"): "stapelimport",
    ("documents", "import_batch_detail"): "stapelimport",
    ("search", "documents"): "dokumente-finden",
    ("workflows", "list"): "aufgaben-workflows",
    ("accounts", "profile_notifications"): "kommentare",
    ("accounts", "profile_reminders"): "dokument-bearbeiten",
    ("accounts", "profile_shortcuts"): "tastenkuerzel",
    ("documents", "settings_metadata_choice_lists"): "administration",
    ("documents", "settings_metadata_choice_list_create"): "administration",
    ("documents", "settings_metadata_choice_list_edit"): "administration",
}


def contextual_help_topic(resolver_match) -> HelpTopic:
    if resolver_match is None:
        return TOPICS_BY_SLUG["erste-schritte"]
    key = (resolver_match.app_name or "", resolver_match.url_name or "")
    slug = CONTEXT_TOPIC_SLUGS.get(key)
    if slug is None:
        if "settings" in (resolver_match.url_name or ""):
            slug = "administration"
        else:
            slug = "erste-schritte"
    return TOPICS_BY_SLUG[slug]


def visible_help_topics(*, user, tenant) -> list[HelpTopic]:
    is_admin = can_administer_tenant(user, tenant)
    topics = []
    for topic in HELP_TOPICS:
        if topic.admin_only and not is_admin:
            continue
        if topic.permission_code and not has_tenant_permission(
            user,
            tenant,
            topic.permission_code,
        ):
            continue
        topics.append(topic)
    return topics
