from __future__ import annotations

from django import forms

from doksio.audit.models import AuditEvent
from doksio.tenancy.models import Tenant


class AuditEventFilterForm(forms.Form):
    query = forms.CharField(
        label="Suche",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ereignis, Objekt oder Benutzer",
            }
        ),
    )
    timestamp_from = forms.DateTimeField(
        label="Zeitstempel von",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    timestamp_to = forms.DateTimeField(
        label="Zeitstempel bis",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M"],
        widget=forms.DateTimeInput(
            attrs={"class": "form-control", "type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
    )
    event_type = forms.ChoiceField(
        label="Ereignistyp",
        required=False,
        choices=[("", "Alle Ereignistypen")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(
        self,
        *args,
        tenant: Tenant,
        event_labels: dict[str, str],
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        event_types = (
            AuditEvent.objects.filter(tenant=tenant)
            .order_by("event_type")
            .values_list("event_type", flat=True)
            .distinct()
        )
        self.fields["event_type"].choices = [
            ("", "Alle Ereignistypen"),
            *[
                (event_type, event_labels.get(event_type, event_type))
                for event_type in event_types
            ],
        ]

    def clean(self) -> dict:
        cleaned_data = super().clean()
        timestamp_from = cleaned_data.get("timestamp_from")
        timestamp_to = cleaned_data.get("timestamp_to")
        if timestamp_from and timestamp_to and timestamp_from > timestamp_to:
            self.add_error(
                "timestamp_to",
                "Der Endzeitpunkt muss nach dem Startzeitpunkt liegen.",
            )
        return cleaned_data
