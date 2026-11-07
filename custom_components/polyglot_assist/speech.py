"""Polyglot's own spoken answers, in the language of the sentence.

A new language is one more entry per key; missing keys fall back to English.
"""
from __future__ import annotations

SPEECH: dict[str, dict[str, str]] = {
    "done": {"de": "Erledigt.", "en": "Done.", "fr": "C'est fait.", "nl": "Gedaan.", "it": "Fatto.", "es": "Hecho."},
    "error": {"de": "Das hat leider nicht geklappt.", "en": "Sorry, that did not work.", "fr": "Désolé, ça n'a pas marché.",
              "nl": "Sorry, dat is niet gelukt.", "it": "Mi dispiace, non ha funzionato.", "es": "Lo siento, no ha funcionado."},
    "handler_failed": {"de": "Der Befehl {name} ist fehlgeschlagen.", "en": "The command {name} failed.",
                       "fr": "La commande {name} a échoué.", "nl": "De opdracht {name} is mislukt.",
                       "it": "Il comando {name} non è riuscito.", "es": "El comando {name} ha fallado."},
    "no_handler": {"de": "Für den Befehl {name} ist keine Aktion eingerichtet.", "en": "No action is set up for the command {name}.",
                   "fr": "Aucune action n'est configurée pour la commande {name}.", "nl": "Er is geen actie ingesteld voor de opdracht {name}.",
                   "it": "Nessuna azione è configurata per il comando {name}.", "es": "No hay ninguna acción configurada para el comando {name}."},
    "fallback_unavailable": {"de": "Mein KI-Assistent ist gerade nicht erreichbar.", "en": "My AI assistant is not available right now.",
                             "fr": "Mon assistant IA n'est pas disponible pour le moment.", "nl": "Mijn AI-assistent is nu niet bereikbaar.",
                             "it": "Il mio assistente IA non è disponibile al momento.", "es": "Mi asistente de IA no está disponible ahora."},
    "fallback_loop": {"de": "Die Weiterleitung führt im Kreis. Bitte den KI-Assistenten in Polyglot prüfen.",
                      "en": "Handing this on would go round in circles. Please check the AI assistant in Polyglot.",
                      "fr": "La transmission tourne en rond. Vérifie l'assistant IA dans Polyglot.",
                      "nl": "Doorsturen draait in een kringetje. Controleer de AI-assistent in Polyglot.",
                      "it": "L'inoltro gira in tondo. Controlla l'assistente IA in Polyglot.",
                      "es": "El reenvío da vueltas en círculo. Revisa el asistente de IA en Polyglot."},
}


def say(key: str, lang: str | None, **kw: str) -> str:
    """Text for ``key`` in ``lang`` (base language of e.g. "de-AT"), else English."""
    texts = SPEECH[key]
    base = (lang or "").split("-")[0].lower()
    text = texts.get(base) or texts["en"]
    return text.format(**kw) if kw else text
