# Library games in Berlin

A phone-friendly list of PS4, PS5, Switch and Switch 2 games in the Berlin public libraries (VÖBB),
with every copy and whether it's on the shelf.

- `crawler.py` reads the VÖBB catalog over plain HTTP and writes `games.json`.
- `.github/workflows/crawl.yml` runs it every night and on the "Run workflow" button.
- `index.html` is the page. GitHub Pages serves it together with `games.json`.

A full run takes about 60 to 90 minutes because the crawler waits between requests.
