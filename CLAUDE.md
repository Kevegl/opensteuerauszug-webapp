# CLAUDE.md

## Agenten-Rollen

Die Hauptsession (Opus 5.5) plant, koordiniert und prüft die Ergebnisse. Die Arbeit gibt sie an Subagenten ab:

| Rolle    | Modell     | Agent-Tool-Parameter | Aufgabe                                              |
|----------|------------|----------------------|------------------------------------------------------|
| Umsetzer | Sonnet 5.5 | `model: "sonnet"`    | Code schreiben und ändern, Features umsetzen, Bugs fixen |
| Tester   | Haiku 5.5  | `model: "haiku"`     | Tests schreiben und ausführen, Ergebnisse melden     |

- Opus delegiert die Umsetzung an den Umsetzer und die Prüfung an den Tester, statt beides selbst zu erledigen.
- Jeder Auftrag an einen Subagenten nennt die betroffenen Dateien und das Abnahmekriterium.
- Opus prüft die Ergebnisse der Subagenten (Diff, Testausgabe), bevor es eine Aufgabe als erledigt meldet.
