# Doksio Agent Instructions

## Help Center Is Part of Every Feature

The in-app help is a maintained product surface, not optional documentation.
For every functional, behavioral, navigational, permission-related, or visible
UI change:

1. Check whether the affected workflow, terminology, instructions, tips, or
   contextual help assignment must change.
2. Update the canonical help catalog in
   `src/doksio/helpcenter/catalog.py` when users need new or revised guidance.
3. Update `CONTEXT_TOPIC_SLUGS` and `contextual_help_topic()` when routes are
   added, renamed, or should show different contextual help.
4. Keep help content in clear German for non-technical employees. Describe the
   user's task and expected result rather than implementation details.
5. Keep permission-restricted topics hidden from users who cannot perform the
   described action.
6. Add or update tests in `tests/test_helpcenter.py` when help content,
   visibility, or contextual assignment changes.

A change is not complete until its impact on the in-app help has been reviewed,
even when no help-file modification is ultimately necessary.

## German Changelog Is Part of Every Visible Change

`CHANGELOG.md` is the canonical source for the changelog shown when users click
the build number. For every user-visible functional, behavioral, navigational,
permission-related, or UI change:

1. Update the current build entry in `CHANGELOG.md` in the same change.
2. Write concise German bullet points for non-technical employees.
3. Put new capabilities under `Neuerungen` and changed behavior or presentation
   under `Änderungen`.
4. Describe user-visible results, not implementation details, internal class
   names, migrations, or test mechanics.
5. Keep the `{{ build_number }}` and `{{ build_datetime }}` placeholders in the
   current entry. They are filled automatically from build metadata.
6. Add or update changelog rendering tests when its format, loading, or UI
   presentation changes.
7. Never overwrite an older build entry. Before starting the next build entry,
   preserve the previous entry with its fixed build number and timestamp and
   keep all historical entries in reverse chronological order.
8. The placeholder entry may contain only changes from the calendar day of the
   current Git build. When the build date changes, move all older-day bullets
   into a fixed historical entry using the last commit timestamp of that day.

A user-visible change is not complete until its changelog impact has been
reviewed, even when no changelog bullet is ultimately necessary.
