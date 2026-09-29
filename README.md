# EOM Grounding для pyRevit

Публичный установочный репозиторий расширения **EOM Grounding**. В корне находится готовая структура pyRevit: `EOM.tab`, `lib`, `resources` и `bundle.yaml`.

## Установка по URL

В менеджере расширений pyRevit выберите установку пользовательского расширения и укажите:

`https://github.com/ivanyurkov90/EOM-Grounding-pyRevit.git`

Имя расширения: `EOM_Grounding`.

Через pyRevit CLI:

```powershell
pyrevit extend ui EOM_Grounding https://github.com/ivanyurkov90/EOM-Grounding-pyRevit.git
pyrevit reload
```

Обновление:

```powershell
pyrevit extensions update
pyrevit reload
```

Перед обновлением рекомендуется закрыть рабочие документы Revit. Если расширение уже было установлено вручную, удалите или переименуйте только старую папку `EOM_Grounding.extension`, чтобы не осталось двух копий команды.

## Текущая сборка

- VERSION: `0.10.42-test-audit-fixes`
- BUILD_ID: `20260929-r31`

Основной репозиторий разработки: [ivanyurkov90/EOM-Grounding](https://github.com/ivanyurkov90/EOM-Grounding).
