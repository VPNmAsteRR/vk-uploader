#!/usr/bin/env python3
"""vkup — локальная загрузка видео в VK Видео (официальный API).

    ./vkup.py                     # интерфейс на http://127.0.0.1:8765
    ./vkup.py --mock              # то же, но VK подменён локальной заглушкой
    ./vkup.py --mock --autostart  # сразу запустить очередь (демо)
    ./vkup.py --port 9000
"""

from vkup.server import main

if __name__ == "__main__":
    raise SystemExit(main())
