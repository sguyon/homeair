# Deployment

For access away from home, use Railway. For a home server or NAS, use Docker Compose. Only Railway and Docker are tested: another host that runs a container (Fly.io, Render, a VPS) can build from the `Dockerfile` with the same settings, but it is untested and these steps don't cover it. Keep `config.toml` and `.env` private. Agents run commands themselves and ask once, before the first change to a live service or account, saying what they will do.

## What each host reads

- **This computer, and a home server with Docker:** the monitors on your Wi-Fi, directly, one reading a minute, with no AirGradient API token. They keep their own history of the readings (`history.sqlite`). A token is optional there: it adds the monitors' cloud ids, fills the last 7 days of history, and is the fallback if a monitor stops answering.
- **Railway, or any host online:** AirGradient's cloud only. Home Air online can't read the monitors on your Wi-Fi, so it needs the AirGradient API token, shows readings up to 5 minutes old, and keeps no history of its own (charts come from AirGradient's cloud). A home without a token adds one first, on its computer's page ([SETUP.md](SETUP.md)); `./homeair railway` refuses to send a home that has none, and leaves the monitors' Wi-Fi addresses out of what it sends.

## Railway

Railway builds from this repo's `Dockerfile`. There is no login on the page: anyone with its address can see the readings and the home's rounded location (about 1 km). Keep the address private; telling search engines not to index it is not access control.

Prepare `config.toml` with [SETUP.md](SETUP.md). The helper below, `./homeair railway`, runs through `scripts/run.sh`, which finds or installs what it needs. Install the [Railway CLI](https://docs.railway.com/guides/cli); the user signs in with `railway login` (`railway whoami` shows who).

`railway init` links the current folder to the new project, and every later `railway` command here uses that link. Run from a folder that has no link yet: `railway status` should say there is no linked project. A folder already linked to another project (a copy of this repo you deploy elsewhere) would send the code to the wrong one; use a fresh clone. With several workspaces, `init` asks which one, or pass `--workspace <name>`. Before the first command that changes Railway, ask once and wait for the yes, with a question tool if you have one (two answers: “Yes, deploy to Railway” and “No, cancel”), and add nothing else around it:

> Deploying to Railway bills your account, and anyone with the link will be able to see your readings and your home's rounded location.
>
> Confirm: create a project and service called homeair in your Railway account, send it your settings and AirGradient API token, deploy the app and create its public address.
>
> 1. Yes, deploy to Railway
> 2. No, cancel

After that yes, run every step below without asking again; only removing it is asked separately. Create the project and service:

```bash
railway init --name homeair
railway add --service homeair
```

`railway list` should show the project once; if the name appears twice, delete only the one you made, by its ID (see removing it below).

Check the AirGradient API token first: Home Air online reads your monitors through AirGradient's Cloud API, so it needs the token. If `./homeair status` says the token is missing, tell the user in one line why, run `./homeair connect` and open the address it prints, then `./homeair connect --wait` until it prints `Connected`.

Send settings using the helper:

```bash
./homeair railway --service homeair
```

It sends `CONFIG_TOML` from `config.toml` and each set key from `.env` through stdin, and prints only the names sent. Neither private file is uploaded with the code. It sets them without starting a deploy (`--skip-deploys`), so no empty or failing deploy starts before the code is up; the next step deploys with all of them. To set one variable yourself the same way, add `--skip-deploys` to `railway variable set`.

Deploy:

```bash
railway up --service homeair --detach
```

`railway up` uploads this folder to the user's own Railway account, skipping what `.gitignore` lists (so not `config.toml` or `.env`); the Dockerfile's `.dockerignore` keeps them out of the image too. `--detach` returns at once, before the build is done. Wait for it: `railway deployment list --service homeair` shows the newest deployment's status; it should change from BUILDING or DEPLOYING to SUCCESS in about 2 to 5 minutes. On FAILED, `railway logs --service homeair --build` shows why; on CRASHED, `railway logs --service homeair`.

Create the address. A bare `railway domain` creates a public one, so check first with `railway domain list --service homeair`; empty means none yet:

```bash
railway domain --service homeair
```

The image listens on the `PORT` Railway sets, so there is no port to choose. Open the URL it prints (`railway domain list --service homeair` shows it again). Run `./homeair check https://<your-domain>` to verify that destination; it should end with "Live OK". If it doesn't answer, check `railway logs --service homeair`. To add it to a phone's home screen, see [On your phone](README.md#on-your-phone). If the Home Air skill is added on your computer, point it at this address: `./homeair skill add --url https://<your-domain>`. To ask claude.ai or ChatGPT about the air at home, add `https://<your-domain>/mcp` as a custom connector in claude.ai or a custom MCP server in ChatGPT ([docs/agents.md](docs/agents.md)).

To remove it all, with the user's OK (it can't be undone): take the linked project's ID, the `id` in `railway status --json`, and run `railway delete --project <id>`. An agent can't answer its prompt, so it adds `--yes`, and `--2fa-code <code>` from the user when the account has two-factor sign-in. Then check that it is gone from `railway list`. The address stops working with it.

After config or key changes, ask once what will change, then rerun the transfer helper, then `railway redeploy --service homeair --yes` to apply them (the helper sets variables without deploying). After code changes, rerun `railway up --service homeair --detach` and wait as above.

## Docker

**On a home server** (a mini PC, a Raspberry Pi or a NAS): set up on your computer first ([SETUP.md](SETUP.md)), then run `./homeair home-server`. It prepares a `home-server` folder (a Compose file using the published image, `config.toml` and a private copy of `.env`) and says the two steps: copy the folder to the server, then run `docker compose up -d` in it. It looks for your monitors on the Wi-Fi again and writes the addresses it finds into the folder's `config.toml`, because a container can't browse the network: the server reads those addresses directly, with no token. If a monitor's address changes (the router gave it a new one), the server can't find it again on its own: with a token it reads that monitor from the cloud, and without one the card shows its last reading and says it isn't answering; run `--home-server` again on your computer and copy the folder over. The page opens at `http://<the server>:8000` for every device at home; it has no password, so anyone on that network can see it. A NAS can take the same Compose file through its own screens.

**On this machine, from the repo** (the steps below): install Docker with Compose 2.24 or newer. Build from this repo's Dockerfile. Keep `.env` and `config.toml` beside `docker-compose.yml`.

If uv or Python 3.11+ is available, follow [SETUP.md](SETUP.md) to prepare those files. Without either, run setup inside the image, mounting this repo so files are written here. This uses a temporary container and does not start a live service. If config does not exist yet, create an empty mount source first with `touch config.toml`:

```bash
docker compose build
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work \
  home-air-quality python scripts/setup.py
```

A container started this way can't reach the monitors on the Wi-Fi, so it reads AirGradient's cloud and needs the token: open `.env` in the user's editor and wait while they paste it (see SETUP.md). The home-server copy above needs none. Ask the location and profiles, then run the same container command with `--address "…" --profiles none`. It writes the config and checks it and the monitors. On Linux, the app's container user must be able to read the mounted config: `chmod 644 config.toml`.

Start it (your own machine, so no extra OK):

```bash
docker compose up -d
```

Open http://localhost:8000. Verify with `docker compose exec home-air-quality python scripts/setup.py --live http://localhost:8000`. If you change only the host port mapping, open that host address in your browser and keep http://localhost:8000 in the container's verification command. Change the verification port only if you also change PORT inside the container. `docker compose logs` shows startup problems.

To open it from a phone on the same network, change the Compose port line to `"8000:8000"`, recreate the service, and use `http://<server-address>:8000`. Anyone on that network can then see the page; it has no password or HTTPS. Don't forward this port on your router. Use Railway or a home VPN for access from outside.

The history of the readings (`history.sqlite`) and the monitors' last known addresses (`local-addresses.json`) are kept in the Docker volume `homeair-history`, mounted at `/data`: they survive `--force-recreate` and a new image. `docker compose down -v` deletes them. With a token, the container fills the last 7 days from the cloud when it starts.

After config or `.env` changes: `docker compose up -d --force-recreate` (recreation reloads environment variables). After pulling code: `docker compose up -d --build`. These restart your own container: say so, no extra OK.
