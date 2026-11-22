# Деплой сервера зашлушений на существующий дроплет

Сервер нужен только для одного: принимать страты с телефонов поваров и
держать их, пока менеджер не заберёт. Никакой истории и никаких отчётов на
нём нет — вся правда в базе на компьютере менеджера.

Ставим **вторым, независимым сервисом** рядом с Beer Count: своя папка, своя
база, свой порт, свой поддомен. Ни один файл Beer Count не трогаем.

| | |
|---|---|
| Дроплет | `46.101.139.239` (BeerCount-HRC, FRA1) |
| Вход | `ssh -i ~/.ssh/beercount_do root@46.101.139.239` |
| Папка | `/opt/kuchnia` |
| Очередь | `/opt/kuchnia/kitchen_queue.db` |
| Порт | `8788` (Beer Count занимает 8777, наружу закрыты оба) |
| Домен | `kuchnia.htsautomation.com` |
| Сервис | `kuchnia.service`, пользователь `deploy` |

> **Шаги 0–5 выполнены 2026-10-22**: сервис `kuchnia` работает, HTTPS
> выпущен (продление проверено `certbot renew --dry-run` вместе с Beer
> Count). Пароль `/admin` сгенерирован на сервере и лежит только в
> `/etc/kuchnia.env`. Осталось — шаг 6 (кухня, токен, QR).
> Команды гоняются с машины, где лежит ключ `~/.ssh/beercount_do`.

---

## 0. DNS (первым — запись должна успеть разойтись)

В панели домена `htsautomation.com`:

```
A   kuchnia   46.101.139.239   TTL 300
```

Проверка: `nslookup kuchnia.htsautomation.com` → 46.101.139.239. Пока не
вернёт — шаг 5 (сертификат) не поедет.

## 1. Папка и venv

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "mkdir -p /opt/kuchnia && chown deploy:deploy /opt/kuchnia && python3 -m venv /opt/kuchnia/venv && /opt/kuchnia/venv/bin/pip install -q --upgrade pip && /opt/kuchnia/venv/bin/pip install -q Flask gunicorn qrcode && /opt/kuchnia/venv/bin/python -c 'import flask, gunicorn, qrcode; print(\"deps OK\")'"
```

## 2. Залить сервер

Едут **три файла** — сервер, форма и иконка. Больше ничего на дроплете не нужно.

```bash
cd "C:\Users\Legion\OneDrive\Робочий стіл\LOSES KITCHEN"
scp -i ~/.ssh/beercount_do server/kitchen_server.py server/form.html server/icon.png root@46.101.139.239:/opt/kuchnia/
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "chown -R deploy:deploy /opt/kuchnia && /opt/kuchnia/venv/bin/python -m py_compile /opt/kuchnia/kitchen_server.py && echo SYNTAX-OK"
```

## 3. systemd

Пароль админки — в отдельном файле окружения, `chmod 600` закрывает только
секрет, а сам юнит остаётся показываемым.

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "printf 'KUCHNIA_ADMIN_PASSWORD=ВЫБЕРИ-ПАРОЛЬ\nKUCHNIA_HTTPS=1\nKUCHNIA_PORT=8788\nKUCHNIA_SERVER_DB=/opt/kuchnia/kitchen_queue.db\n' > /etc/kuchnia.env && chmod 600 /etc/kuchnia.env"
```

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "cat > /etc/systemd/system/kuchnia.service <<'EOF'
[Unit]
Description=Straty Kuchnia — serwer zgloszen
After=network.target

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/opt/kuchnia
EnvironmentFile=/etc/kuchnia.env
ExecStart=/opt/kuchnia/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8788 --access-logfile - kitchen_server:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now kuchnia && sleep 2 && systemctl is-active kuchnia && curl -s http://127.0.0.1:8788/"
```

Ждём `active` и строку `Straty Kuchnia — serwer zgłoszeń 1.0.0 OK`.

## 4. nginx

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "cat > /etc/nginx/sites-available/kuchnia <<'EOF'
server {
    listen 80;
    server_name kuchnia.htsautomation.com;
    client_max_body_size 1m;
    location / {
        proxy_pass http://127.0.0.1:8788;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/kuchnia /etc/nginx/sites-enabled/kuchnia && nginx -t && systemctl reload nginx"
```

`nginx -t` должен сказать `syntax is ok` — только тогда reload.

## 5. HTTPS

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "certbot --nginx -d kuchnia.htsautomation.com --non-interactive --agree-tos -m roberthurshudyan@gmail.com --redirect && systemctl reload nginx"
```

Проверка снаружи:

```bash
curl -s https://kuchnia.htsautomation.com/
```

**HTTPS обязателен, а не «желательно»:** без него телефон не даст поставить
форму на главный экран и не включит офлайн-режим, то есть повар при
пропавшем Wi-Fi просто не откроет страницу.

## 6. Завести кухню и связать с приложением

1. `https://kuchnia.htsautomation.com/admin`, пароль из `/etc/kuchnia.env`.
2. «Nowa kuchnia» → `HRC Warszawa — kuchnia`. Скопировать **токен**.
3. В приложении: `Ustawienia → Serwer` → адрес `https://kuchnia.htsautomation.com`,
   токен, **Zapisz** → **Sprawdź połączenie** (ждём зелёное `✓ Połączono`).
4. Там же **Wyślij katalog na serwer** — иначе форма у поваров будет пустая.
5. В `/admin` распечатать QR и повесить на кухне.

> **Каталог не синхронизируется сам.** После каждой правки позиций или
> причин — снова «Wyślij katalog na serwer». Это осознанно: менеджер сам
> решает, когда кухня увидит изменения.

## 7. Бэкапы

Очередь на сервере — расходный материал (30 дней и удаляется само), терять
её не страшно. **Резервировать нужно базу на компьютере менеджера:**
`Ustawienia → O programie → Kopia zapasowa bazy`, раз в месяц на диск или в
OneDrive. Файл `kuchnia.db` — это всё, что есть.

Если всё же хочется снимка очереди:

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "sqlite3 /opt/kuchnia/kitchen_queue.db \".backup '/opt/kuchnia/kitchen_queue.bak.db'\" && ls -la /opt/kuchnia/*.db"
```

---

## Обновление сервера

```bash
cd "C:\Users\Legion\OneDrive\Робочий стіл\LOSES KITCHEN"
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "cd /opt/kuchnia && cp -a kitchen_server.py kitchen_server.py.bak-$(date +%F)"
scp -i ~/.ssh/beercount_do server/kitchen_server.py server/form.html root@46.101.139.239:/opt/kuchnia/
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "chown -R deploy:deploy /opt/kuchnia && /opt/kuchnia/venv/bin/python -m py_compile /opt/kuchnia/kitchen_server.py && systemctl restart kuchnia && sleep 2 && systemctl is-active kuchnia"
```

`py_compile` гоняется, пока старый процесс ещё жив: про кривой синтаксис
узнаём до рестарта, а не после.

Правил `form.html`? Подними `VERSION` в `kitchen_server.py` — телефоны
кэшируют форму, и без этого повар увидит старую до очистки кэша.

## Откат

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "cd /opt/kuchnia && cp -a kitchen_server.py.bak-ДАТА kitchen_server.py && systemctl restart kuchnia && systemctl is-active kuchnia"
```

## Если что-то не так

```bash
ssh -i ~/.ssh/beercount_do root@46.101.139.239 "journalctl -u kuchnia -n 50 --no-pager"
```

| Симптом | Куда смотреть |
|---|---|
| `502` от nginx | сервис лежит: `systemctl status kuchnia` и журнал выше |
| «Brak dostępu» у повара | токен из QR не совпадает или кухня выключена в `/admin` |
| Форма пустая | не нажата «Wyślij katalog na serwer» в приложении |
| В приложении «Zły token» | токен из `/admin` не тот, либо кухню пересоздали |
| «Serwer niedostępny» | нет интернета у ПК менеджера или сервис не поднят |
| Зашлушения не приходят | у повара «⏳ czeka» — телефон без сети; уедут сами |
