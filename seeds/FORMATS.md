# Форматы данных (seeds/)

## working_mails.txt (t-online пул)
`email:password` по строке, canonical-адреса (dash-алиасы мёртвы для доставки).
Скопировать в data/working_mails.txt

## gh_accounts.json (GitHub OAuth пул)
[{"email":"...","password":"...","username":"...","totp":"...","recovery":["..."]}, ...]
Акки должны быть с verified email, иначе device-flow "Github Error - 500".
Скопировать в data/gh_accounts.json

## proxies.txt
`host:port` или `http://user:pass@host:port` по строке.
Скопировать в data/proxies.txt (или нажать "ЗАБРАТЬ БЕСПЛАТНЫЕ" в дашборде)

## config.json
Скопировать config.example.json → config.json, заполнить секреты (2captcha, IMAP, telegram api_id/hash).
Config.json в .gitignore — не коммитить.
