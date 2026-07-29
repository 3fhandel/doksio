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
