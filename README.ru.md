# P3R Save Transfer Tool

[![CI](https://github.com/Thunderouse/p3r-save-transfer/actions/workflows/ci.yml/badge.svg)](https://github.com/Thunderouse/p3r-save-transfer/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Thunderouse/p3r-save-transfer?include_prereleases&label=release)](https://github.com/Thunderouse/p3r-save-transfer/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Перенос сохранений **Persona 3 Reload** из версии Microsoft Store / Xbox Game Pass
в Steam, чтобы продолжить прохождение после покупки игры в Steam.

[English version](README.md) · [Changelog](CHANGELOG.md) · [Notice](NOTICE.md)

Утилита меняет только **обёртку**: снимает (или ставит) ту простую обфускацию,
которой Steam-версия закрывает обычный Unreal-контейнер `GVAS`. Содержимое
сохранения - прогресс, инвентарь, соцлинки, персоны - не редактируется и не
изменяется. Каталог Microsoft Store открывается только на чтение.

```
MS Store:  <файл с мусорным именем>   GVAS ......        (открытый контейнер)
Steam:     SaveDataNNN.sav           0B 65 00 15 ...    (тот же контейнер + обфускация)
```

## Скриншоты

![Список сохранений](docs/screenshots/gui-found.png)

![Конвертация завершена](docs/screenshots/gui-success.png)

## Требования

- Windows 10 или Windows 11, x64
- Python для portable-сборки не нужен
- Сборка на Tkinter несёт свой рантайм: WebView2, .NET и Visual C++
  Redistributable **не требуются**
- Права администратора не нужны: всё происходит в пользовательских папках

## Установка (portable-zip)

1. Откройте [последний релиз](https://github.com/Thunderouse/p3r-save-transfer/releases)
   и скачайте `P3R-Save-Transfer-0.1.0-beta-win64-portable.zip` и файл `.sha256`.
2. Распакуйте **всю папку** в любое место: рабочий стол, `D:\Tools`, флешку.
   Пробелы и русские буквы в пути допустимы.
3. Проверьте хэш (необязательно, но полезно):

   ```powershell
   Get-FileHash .\P3R-Save-Transfer-0.1.0-beta-win64-portable.zip -Algorithm SHA256
   ```

   ```bat
   certutil -hashfile P3R-Save-Transfer-0.1.0-beta-win64-portable.zip SHA256
   ```

4. Запустите `P3R-Save-Transfer.exe`.

Exe **не подписан**, поэтому SmartScreen при первом запуске может показать
«Windows защитила ваш компьютер»: «Подробнее» -> «Выполнить в любом случае».
Хэш из описания релиза позволяет убедиться, что скачан именно опубликованный файл.

## Как пользоваться

### GUI (три клика)

1. Закройте игру и приложение Xbox.
2. Нажмите **«Найти автоматически»** - утилита смотрит в
   `%LOCALAPPDATA%\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs`.
   Можно также перетащить папку `wgs` или файл `.sav` в окно либо нажать `Ctrl+O`.
3. Отметьте сохранения и нажмите **«Конвертировать»**. Папка Steam
   (`%APPDATA%\Sega\P3R\Steam\<SteamID64>`) определяется автоматически; если
   профилей несколько - выберите нужный в списке «Аккаунт». Если файл уже есть,
   появится подтверждение перезаписи.
4. Нажмите **«Открыть папку»** и запустите Steam-версию.

Горячие клавиши: `Ctrl+O` - выбрать папку, `F5` - пересканировать,
`Ctrl+Enter` - конвертировать/отменить, `Esc` - закрыть помощь/модал/отменить.
`Tab` обходит все элементы, `Enter`/`Space` активируют.

Настройки лежат в `config.json` рядом с exe; если папка только для чтения -
в `%LOCALAPPDATA%\P3R-Save-Transfer\config.json`, и это пишется в лог.

В каталог Microsoft Store утилита не пишет никогда, а перед записью в Steam
создаёт бэкап (`_p3r_backup_<timestamp>` с манифестом SHA-256).

### CLI

`p3r-save-transfer-cli.exe` лежит в той же папке и повторяет Python-CLI:

```powershell
.\p3r-save-transfer-cli.exe scan
.\p3r-save-transfer-cli.exe convert-all --wgs "$env:LOCALAPPDATA\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs" --out-dir "$env:USERPROFILE\p3r_out" --dry-run
.\p3r-save-transfer-cli.exe verify "$env:USERPROFILE\p3r_out\SaveData001.sav"
```

```bat
p3r-save-transfer-cli.exe scan
p3r-save-transfer-cli.exe convert-all --wgs "%LOCALAPPDATA%\Packages\SEGAofAmericaInc.L0cb6b3aea_s751p9cej88mt\SystemAppData\wgs" --out-dir "%USERPROFILE%\p3r_out"
p3r-save-transfer-cli.exe install --in "%USERPROFILE%\p3r_out\SaveData001.sav" --slot 1
```

Из исходников: `py -3 -m pip install .` ставит команду `p3r-save-transfer`.

| Команда | Что делает |
|---|---|
| `scan [--wgs PATH] [--json] [--steam]` | находит сохранения: слот, дата, уровень, время, размер, тип |
| `convert --in FILE --out-dir DIR [--slot N] [--reverse] [--dry-run] [--force]` | конвертирует один файл |
| `convert-all --wgs PATH --out-dir DIR [--dry-run] [--force]` | конвертирует всё найденное |
| `verify FILE...` | magic, размер и побайтовый round-trip |
| `install --in FILE --slot N [--steam-id ID]` | копирует прямо в Steam-профиль |

Коды возврата: `0` - успех, `1` - проблема (файл занят, слот занят, проверка не
прошла), `2` - ошибка в аргументах.

## Как это работает

Steam-сохранение - тот же контейнер Unreal `GVAS`, к каждому байту которого
применены два простых преобразования с фиксированным 31-байтным ключом,
одинаковым во всех копиях игры:

1. перестановка битовых пар 0-1 и 4-5 внутри байта;
2. XOR результата с очередным байтом ключа (ключ повторяется).

Декодирование делает XOR первым, затем ту же перестановку. Преобразование
обратно само себе и не меняет размер файла, поэтому конвертация побайтово
безопасна - и утилита это доказывает: перед записью она применяет обратное
преобразование к результату и сравнивает с исходником. То же самое показывает
`p3r-save-transfer verify`.

## Если что-то пошло не так

| Симптом | Что делать |
|---|---|
| `Файл занят или недоступен` | Закройте игру и приложение Xbox, подождите пару секунд, повторите. |
| `Каталог wgs не найден` | Игра из Microsoft Store не запускалась на этом ПК или `LOCALAPPDATA` указывает на другой диск: передайте `--wgs` или выберите папку вручную. |
| `Неизвестный magic 0x...` | Файл не является сохранением P3R (или это служебный `container.*`). |
| `Слот N уже занят` | Возьмите подсказанный свободный слот или разрешите перезапись (`--force` сначала делает бэкап). |
| `n/a` в метаданных | Поле не удалось прочитать для этой версии формата. Конвертация работает. |
| Кракозябры в перенаправленном файле | CLI пишет UTF-8 при перенаправлении: откройте файл в UTF-8-редакторе или выполните `chcp 65001`. |

## Известные ограничения

- **Достижения не переносятся.** Достижения Game Pass остаются на Xbox;
  достижения Steam начнут открываться с момента загрузки сконвертированного слота.
- **Облачные сохранения сначала должны быть на диске.** Установите и один раз
  запустите игру со своим аккаунтом Xbox, дайте синхронизации пройти, закройте
  игру - только потом запускайте утилиту. В аккаунты она не логинится.
- **Номера слотов в контейнерах Microsoft Store не хранятся.** `convert-all`
  назначает их по игровому времени; если порядок важен - задайте `--slot` вручную.
- **Обратное направление** (`--reverse`) даёт открытый `GVAS`. Это для проверки и
  ручных экспериментов: раскладке Microsoft Store нужен ещё и служебный
  `container.*`, поэтому автоматического «install в Xbox» нет.
- **Это beta.** Инструмент собран и проверен одним человеком на ограниченном
  наборе сохранений. Свои бэкапы всё равно держите.

## Дисклеймер

Неофициальный фанатский инструмент. Не связан с Atlus, SEGA, Microsoft и Valve;
торговые марки принадлежат правообладателям. В репозитории нет игровых ассетов,
шрифтов игры и файлов сохранений. Предназначен для конвертации **своих**
сохранений между купленными копиями игры и не является обходом DRM. Подробнее -
[NOTICE.md](NOTICE.md).

## Разработка

```powershell
py -3 -m pip install -e ".[dev]"
py -3 -m pytest
py -3 -m ruff check .
py -3 tools\windows_smoke.py     # сквозная проверка, только Windows
powershell -ExecutionPolicy Bypass -File tools\build_portable.ps1
```

Структура: `src/p3r_transfer` (движок: codec, discovery, convert, meta, CLI),
`ui/` (окно на Tkinter поверх `ui/bridge.py`), `tools/` (сборка, guard, smoke),
`tests/`.

## Лицензия

MIT - см. [LICENSE](LICENSE).
