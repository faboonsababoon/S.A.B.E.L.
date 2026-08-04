"""Conservative confirmation rules for destructive local actions."""


TRASH_WARNING = (
    "Emptying the Trash permanently deletes its current contents. "
    'To continue, explicitly say something like "Yes, empty the Trash."'
)


def is_explicit_trash_confirmation(user_text: str) -> bool:
    """Require both clear assent and a reference to the destructive action."""
    lowered = user_text.casefold()
    assent = "confirm" in lowered or "yes" in lowered
    action_reference = "trash" in lowered and (
        "empty" in lowered or "delete" in lowered or "clear" in lowered
    )
    return assent and action_reference

