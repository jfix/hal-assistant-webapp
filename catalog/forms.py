from __future__ import annotations

from datetime import date

from django import forms
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from catalog.integrations.hal_assistant import HAL_DOCUMENT_TYPE_LABELS_FR


class HALCredentialForm(forms.Form):
    login = forms.CharField(
        label=_("Identifiant HAL"),
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "username"}),
    )
    password = forms.CharField(
        label=_("Mot de passe HAL"),
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "new-password"}, render_value=False
        ),
        help_text=_("Le mot de passe enregistré n’est jamais affiché."),
    )


class InterfaceLanguageForm(forms.Form):
    language = forms.ChoiceField(
        label=_("Langue de l’interface"),
        choices=(
            ("", _("Automatique — langue du navigateur")),
            ("fr", _("Français")),
            ("en", "English"),
        ),
        help_text=_(
            "Le français est utilisé si la langue du navigateur n’est pas prise en charge."
        ),
    )


class ManualPublicationForm(forms.Form):
    hal_document_type = forms.ChoiceField(
        label=_("Type de publication HAL"),
        choices=[
            (code, format_lazy("{code} — {label}", code=code, label=label))
            for code, label in HAL_DOCUMENT_TYPE_LABELS_FR.items()
        ],
    )
    title = forms.CharField(label=_("Titre"), max_length=1000)
    authors = forms.CharField(
        label=_("Auteurs"),
        help_text=_(
            "Ajoutez librement un auteur ou choisissez une forme existante dans HAL. "
            "Séparez plusieurs auteurs par un point-virgule."
        ),
        widget=forms.TextInput(
            attrs={
                "placeholder": "Prénom Nom ; Prénom Nom",
                "autocomplete": "off",
                "data-reference-typeahead": "author",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "author-reference-results",
            }
        ),
    )
    publication_year = forms.IntegerField(
        label=_("Année"),
        min_value=1000,
        max_value=date.today().year + 1,
    )
    language = forms.ChoiceField(
        label=_("Langue principale"),
        choices=(
            ("fr", _("Français")),
            ("en", _("Anglais")),
            ("de", _("Allemand")),
            ("es", _("Espagnol")),
            ("it", _("Italien")),
            ("da", _("Danois")),
            ("no", _("Norvégien")),
            ("sv", _("Suédois")),
        ),
    )
    doi = forms.CharField(
        label=_("DOI (facultatif)"),
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "10.…"}),
    )
    journal_title = forms.CharField(
        label=_("Revue"),
        max_length=1000,
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "data-reference-typeahead": "journal",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "journal-reference-results",
            }
        ),
    )
    journal_hal_id = forms.CharField(required=False, widget=forms.HiddenInput())
    journal_issn = forms.CharField(required=False, widget=forms.HiddenInput())
    journal_publisher = forms.CharField(required=False, widget=forms.HiddenInput())
    book_title = forms.CharField(
        label=_("Titre de l’ouvrage"),
        max_length=1000,
        required=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "data-reference-typeahead": "book",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "book-reference-results",
            }
        ),
    )
    conference_title = forms.CharField(
        label=_("Nom du congrès"),
        max_length=1000,
        required=False,
        widget=forms.TextInput(attrs={"list": "conference-suggestions"}),
    )
    conference_start_date = forms.DateField(
        label=_("Date de début"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    conference_end_date = forms.DateField(
        label=_("Date de fin"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    conference_city = forms.CharField(
        label=_("Ville"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"list": "city-suggestions"}),
    )
    conference_country = forms.CharField(
        label=_("Pays"),
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"list": "country-suggestions"}),
    )

    REQUIRED_BY_TYPE = {
        "ART": ("journal_title",),
        "COUV": ("book_title",),
        "COMM": (
            "conference_title",
            "conference_start_date",
            "conference_end_date",
            "conference_city",
            "conference_country",
        ),
    }

    def clean_authors(self) -> list[str]:
        authors = [
            item.strip()
            for item in self.cleaned_data["authors"].replace("\n", ";").split(";")
            if item.strip()
        ]
        if not authors:
            raise forms.ValidationError("Indiquez au moins un auteur.")
        return authors

    def clean_doi(self) -> str:
        value = self.cleaned_data["doi"].strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if value.lower().startswith(prefix):
                value = value[len(prefix) :].strip()
                break
        return value

    def clean(self):
        cleaned = super().clean()
        document_type = cleaned.get("hal_document_type")
        for field_name in self.REQUIRED_BY_TYPE.get(document_type, ()):
            if not cleaned.get(field_name):
                self.add_error(field_name, "Ce champ est requis pour ce type HAL.")
        start = cleaned.get("conference_start_date")
        end = cleaned.get("conference_end_date")
        if start and end and end < start:
            self.add_error(
                "conference_end_date",
                "La date de fin doit être postérieure ou égale à la date de début.",
            )
        if document_type != "ART":
            for field_name in ("journal_hal_id", "journal_issn", "journal_publisher"):
                cleaned[field_name] = ""
        return cleaned
