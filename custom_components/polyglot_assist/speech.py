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
    "learn_list_one": {"de": 'Ein Vorschlag: {items} Sag zum Beispiel: übernimm den ersten.', "en": 'One suggestion: {items} Say for example: accept the first one.', "fr": 'Une suggestion : {items} Dis par exemple : accepte la première.'},
    "origin_monitor": {"de": "aus der Überwachung", "en": "from monitoring", "fr": "de la surveillance"},
    "origin_translation": {"de": "aus der Übersetzung", "en": "from translation", "fr": "de la traduction"},
    "origin_starter": {"de": "aus dem Grundstock", "en": "from the starter set", "fr": "du kit de départ"},
    "origin_new_devices": {"de": "von neuen Geräten", "en": "from new devices", "fr": "des nouveaux appareils"},
    "learn_reject_panel": {"de": "Verwerfen geht nur im Panel. Per Sprache kann ich übernehmen oder es lassen.", "en": "Discarding is only done in the panel. By voice I can accept or leave it.", "fr": "Rejeter ne se fait que dans le panneau. À la voix, je peux accepter ou laisser."},
    "learn_which": {"de": "Welchen meinst du? Sag zum Beispiel: übernimm den zweiten.", "en": "Which one? Say for example: accept the second one.", "fr": "Laquelle ? Dis par exemple : accepte la deuxième."},
    "learn_none": {"de": 'Gerade gibt es keine Vorschläge, die ich per Sprache übernehmen darf.', "en": 'There are no suggestions I may accept by voice right now.', "fr": "Il n'y a pour l'instant aucune suggestion que je peux accepter à la voix."},
    "learn_list": {"de": '{count} Vorschläge: {items} Sag zum Beispiel: übernimm den ersten.', "en": '{count} suggestions: {items} Say for example: accept the first one.', "fr": '{count} suggestions : {items} Dis par exemple : accepte la première.'},
    "learn_item_sentence": {"de": '„{sentence}“ für {command}', "en": '“{sentence}” for {command}', "fr": '« {sentence} » pour {command}'},
    "learn_item_name": {"de": '„{names}“ als Name für {target}', "en": '“{names}” as a name for {target}', "fr": '« {names} » comme nom pour {target}'},
    "learn_detail": {"de": '{what}. Herkunft: {origin}.', "en": '{what}. Source: {origin}.', "fr": '{what}. Origine : {origin}.'},
    "learn_confirm_accept": {"de": 'Soll ich {what} übernehmen? Sag ja.', "en": 'Shall I accept {what}? Say yes.', "fr": 'Dois-je accepter {what} ? Dis oui.'},
    "learn_confirm_accept_monitor": {"de": 'Das wurde {count}-mal gesagt. Soll ich {what} übernehmen? Sag ja.', "en": 'This came up {count} times. Shall I accept {what}? Say yes.', "fr": "C'est arrivé {count} fois. Dois-je accepter {what} ? Dis oui."},
    "learn_confirm_reject": {"de": 'Soll ich {what} verwerfen? Sag ja.', "en": 'Shall I discard {what}? Say yes.', "fr": 'Dois-je rejeter {what} ? Dis oui.'},
    "learn_confirm_undo": {"de": 'Soll ich {what} wieder entfernen? Sag ja.', "en": 'Shall I remove {what} again? Say yes.', "fr": 'Dois-je retirer {what} ? Dis oui.'},
    "learn_accepted": {"de": 'Übernommen. Mit „mach das rückgängig“ kannst du es innerhalb von 24 Stunden zurücknehmen.', "en": 'Done. “Undo that” takes it back within 24 hours.', "fr": "C'est fait. « Annule ça » revient en arrière pendant 24 heures."},
    "learn_rejected": {"de": 'Verworfen.', "en": 'Discarded.', "fr": 'Rejetée.'},
    "learn_undone": {"de": 'Rückgängig gemacht.', "en": 'Undone.', "fr": 'Annulé.'},
    "learn_none_panel": {"de": 'Per Sprache darf ich gerade nichts übernehmen. Im Panel warten {count} Vorschläge auf dich.', "en": 'There is nothing I may accept by voice right now. {count} suggestions are waiting for you in the panel.', "fr": "Je ne peux rien accepter à la voix pour l'instant. {count} suggestions t'attendent dans le panneau."},
    "learn_none_panel_one": {"de": 'Per Sprache darf ich gerade nichts übernehmen. Im Panel wartet ein Vorschlag auf dich.', "en": 'There is nothing I may accept by voice right now. One suggestion is waiting for you in the panel.', "fr": "Je ne peux rien accepter à la voix pour l'instant. Une suggestion t'attend dans le panneau."},
    "learn_none_listening": {"de": 'Gerade gibt es nichts Neues. Ich werte deine Sätze jeden Tag um {time} Uhr aus.', "en": 'Nothing new right now. I review what you said every day at {time}.', "fr": "Rien de nouveau pour l'instant. J'analyse tes phrases chaque jour à {time}."},
    "learn_none_monitor_off": {"de": 'Gerade gibt es nichts Neues. Schalte im Panel die Überwachung ein, dann lerne ich aus deinen Sätzen.', "en": "Nothing new right now. Turn on monitoring in the panel and I'll learn from what you say.", "fr": "Rien de nouveau pour l'instant. Active la surveillance dans le panneau et j'apprendrai de tes phrases."},
    "learn_none_no_ai": {"de": 'Gerade gibt es nichts Neues. Wähle im Panel eine KI-Aufgabe ohne Werkzeuge, dann kann ich aus deinen Sätzen lernen.', "en": 'Nothing new right now. Choose an AI task without tools in the panel so I can learn from what you say.', "fr": "Rien de nouveau pour l'instant. Choisis dans le panneau une tâche IA sans outils pour que je puisse apprendre de tes phrases."},
    "learn_none_yaml": {"de": 'Lernen geht nur mit dem Katalog als Quelle. Gerade liest Polyglot die Befehle aus intents.yaml.', "en": 'Learning needs the catalog as source. Polyglot currently reads the commands from intents.yaml.', "fr": "L'apprentissage nécessite le catalogue comme source. Polyglot lit actuellement les commandes depuis intents.yaml."},
    "learn_more_in_panel": {"de": 'Im Panel warten noch {count} weitere.', "en": '{count} more are waiting in the panel.', "fr": "{count} autres attendent dans le panneau."},
    "learn_nothing_changed": {"de": 'Gut, ich habe nichts geändert.', "en": 'OK, nothing was changed.', "fr": "D'accord, rien n'a changé."},
    "learn_later": {"de": 'Gut, später.', "en": 'OK, later.', "fr": "D'accord, plus tard."},
    "learn_expired": {"de": 'Die Frage ist abgelaufen. Frag noch einmal.', "en": 'That question has expired. Please ask again.', "fr": 'La question a expiré. Redemande.'},
    "learn_ask_list_first": {"de": 'Frag zuerst: was hast du gelernt?', "en": 'First ask: what have you learned?', "fr": "Demande d'abord : qu'as-tu appris ?"},
    "learn_no_such": {"de": 'So viele Vorschläge gibt es nicht.', "en": "There aren't that many suggestions.", "fr": "Il n'y a pas autant de suggestions."},
    "learn_gone": {"de": 'Diesen Vorschlag gibt es nicht mehr.', "en": 'That suggestion no longer exists.', "fr": "Cette suggestion n'existe plus."},
    "learn_panel_only": {"de": 'Das geht nur im Panel.', "en": 'That can only be done in the panel.', "fr": 'Cela ne peut se faire que dans le panneau.'},
    "learn_rate": {"de": 'Für diese Stunde habe ich genug gelernt. Bitte später oder im Panel.', "en": "That's enough learning for this hour. Please later or in the panel.", "fr": "J'ai assez appris pour cette heure. Plus tard ou dans le panneau."},
    "learn_failed": {"de": 'Das hat nicht geklappt, es wurde nichts geändert.', "en": "That didn't work; nothing was changed.", "fr": "Ça n'a pas marché, rien n'a changé."},
    "learn_nothing_to_undo": {"de": 'Es gibt nichts, was ich rückgängig machen kann.', "en": 'There is nothing I can undo.', "fr": "Il n'y a rien à annuler."},
}


def say(key: str, lang: str | None, **kw: str) -> str:
    """Text for ``key`` in ``lang`` (base language of e.g. "de-AT"), else English."""
    texts = SPEECH[key]
    base = (lang or "").split("-")[0].lower()
    text = texts.get(base) or texts["en"]
    return text.format(**kw) if kw else text
