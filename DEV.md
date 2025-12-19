# Setup dev environment

## Linux or Git Bash on Windows

```bash
curl -fsSL https://deno.land/install.sh | sh    # install Deno JS Runtime https://github.com/yt-dlp/yt-dlp/wiki/EJS
poetry config virtualenvs.in-project true       # virtualenv in the same directory
poetry install --with dev                       # install dependancy packages
poetry run gridplayer                           # run
```

## How to release

- Update CHANGELOG.md with descriptions of the new version
- Bump version, finalize changelog, commit and tag the release

```bash
npx standard-version [--dry-run]                # use --dry-run to see changes before applying
```
