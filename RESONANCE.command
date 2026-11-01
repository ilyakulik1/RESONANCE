#!/bin/bash
# Двойной щелчок в Finder запускает плеер из .venv. Ничего не компилирует.
# Окно Terminal живёт вместе с программой и закрывается, когда она завершается.

cd "$(dirname "$0")" || exit 1

LOG="${HOME}/.simple_audio_player/launch.log"
mkdir -p "$(dirname "$LOG")"

export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
export PYTHONUNBUFFERED=1

SHELL_TTY=$(tty 2>/dev/null || true)
WIN_ID=""
if [[ -n "$SHELL_TTY" ]]; then
  WIN_ID=$(osascript - "$SHELL_TTY" <<'EOF' 2>/dev/null || true
on run argv
  set targetTty to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      try
        if (tty of selected tab of w) is equal to targetTty then
          return id of w as text
        end if
      end try
    end repeat
  end tell
  return ""
end run
EOF
)
fi

close_terminal_window() {
  [[ -n "$WIN_ID" ]] || return 0
  # Сначала завершается этот shell, затем окно закрывается уже без вопроса
  # «завершить процессы?».
  nohup osascript - "$WIN_ID" <<'EOF' >/dev/null 2>&1 &
on run argv
  set winId to (item 1 of argv) as integer
  delay 0.5
  tell application "Terminal"
    repeat with w in windows
      try
        if (id of w) is equal to winId then
          close w
          exit repeat
        end if
      end try
    end repeat
  end tell
end run
EOF
  disown 2>/dev/null || true
}

if [[ ! -x ".venv/bin/python" ]]; then
  osascript -e 'display alert "RESONANCE" message "Не найден .venv/bin/python в папке проекта."'
  close_terminal_window
  exit 1
fi

".venv/bin/python" main.py >>"$LOG" 2>&1
status=$?
if [[ $status -ne 0 ]]; then
  osascript -e "display alert \"RESONANCE\" message \"Программа завершилась с ошибкой (код ${status}). Подробности: ${LOG}\""
fi
close_terminal_window
exit $status
