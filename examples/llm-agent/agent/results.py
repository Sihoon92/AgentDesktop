"""The refusal shape every tool returns when the harness does not run a call."""


def refused(reason, code=None):
    """A safe-to-retry refusal; `code` is the refused_code the model and the viewer can branch on."""
    refusal = {
        "ok": False,
        "refused": reason,
        "disposition": {"delivery": "not_delivered", "retry": "safe"},
    }
    if code is not None:
        refusal["refused_code"] = code
    return refusal
