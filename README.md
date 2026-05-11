# georgiaeconomics.com

Live Georgia state economic data — county heat maps, MSA dashboards, Port of Savannah, film industry, agriculture, labor markets. Sister site to [economicsguru.com](https://economicsguru.com).

## Architecture

- **Static GitHub Pages site** (no build step)
- **Chart.js** for line/bar charts, loaded from the economicsguru repo via jsDelivr:
  `https://cdn.jsdelivr.net/gh/gteconomist/economicsguru@main/charts.js`
  → fixes to charting on economicsguru propagate automatically.
- **Plotly.js** for county-level choropleth maps and MSA comparisons (this site only).
- **maps.js** in this repo wraps Plotly with GA-specific helpers (palette, color scales, leaderboards).
- **GitHub Actions** fetch fresh data monthly into `data/*.json`, then commit it back.

## Repo layout

```
.
├── index.html                      # Landing page
├── styles.css                      # Shared site styling (navy + peach palette)
├── maps.js                         # Plotly choropleth wrappers (GA-specific)
├── CNAME                           # georgiaeconomics.com
│
├── counties/index.html             # County heat map (LIVE in v0 with fixture data)
├── msa/index.html                  # MSA dashboard (coming soon)
├── labor/, housing/, gdp/, etc.    # Topic pages (coming soon)
├── trade/index.html                # Port of Savannah, ATL airport (coming soon)
├── industries/film, agriculture, automotive
│
├── data/
│   ├── counties.json               # FIPS-keyed county data, 12-month frames
│   └── ... (more added by workflows)
│
├── scripts/
│   ├── _ga_counties.py             # Canonical 159-county FIPS list
│   ├── fetch_bls_laus.py           # County unemployment from BLS LAUS
│   └── ... (more added per topic)
│
└── .github/workflows/
    ├── update-labor.yml            # Monthly BLS LAUS county fetch
    └── ... (more added per topic)
```

## v0 setup — first deploy

1. **Create the GitHub repo**
   - On github.com, click "New repository".
   - Owner: `gteconomist` (same as economicsguru).
   - Name: `georgiaeconomics`.
   - Public, no README/license/.gitignore (we already have files).
   - Click "Create repository".

2. **Upload these files**
   - On the new empty repo page, click "uploading an existing file".
   - Drag the entire contents of this `georgiaeconomics/` folder into the upload area.
   - Commit message: "v0: site scaffold with counties heatmap (fixture data)".
   - Commit directly to `main`.

3. **Add the BLS API secret**
   - Repo → Settings → Secrets and variables → Actions → New repository secret.
   - Name: `BLS_API_KEY`. Value: (from your `API keys.docx`).

4. **Enable GitHub Pages**
   - Repo → Settings → Pages.
   - Source: "Deploy from a branch". Branch: `main`, folder: `/ (root)`. Save.
   - Wait ~1 minute. Pages will publish to `https://gteconomist.github.io/georgiaeconomics/`.

5. **Point the domain**
   - The `CNAME` file already contains `georgiaeconomics.com`.
   - At your domain registrar (where you bought georgiaeconomics.com), add these DNS records:
     - `A` records pointing the apex to GitHub Pages IPs:
       `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`
     - `CNAME` record: `www` → `gteconomist.github.io`
   - Back on the Pages settings, verify the custom domain `georgiaeconomics.com`.
   - Tick "Enforce HTTPS" once the certificate provisions (~10 minutes).

6. **Verify v0**
   - Visit `https://georgiaeconomics.com/counties/`.
   - You should see a Georgia county choropleth with a peach-deep-navy color ramp and a 12-month time slider.
   - The peach "Seed data" banner confirms it's currently using fixture values.

7. **Switch from fixture to live data**
   - Repo → Actions tab → "Update GA labor data (BLS LAUS)" → Run workflow.
   - When it finishes green, `data/counties.json` will be the real BLS pull and the fixture banner disappears.

## How to add a new topic page

1. Copy `counties/index.html` to `<topic>/index.html`. Update title, nav active state, headings.
2. Create `data/<topic>.json` with the schema your page expects.
3. Add `scripts/fetch_<topic>.py` to populate the JSON.
4. Add `.github/workflows/update-<topic>.yml` modeled on `update-labor.yml`.
5. Move the topic card on `index.html` from `.coming` to `.feature` (or default).

## Notes for collaborators

- All edits happen through the GitHub web UI (no local dev required).
- Don't copy `charts.js` into this repo — it stays canonical in `gteconomist/economicsguru` and we pull it via jsDelivr.
- `maps.js` is GA-specific and lives here.
- Color palette: navy + mustard + teal (shared with economicsguru) plus a peach accent for state-distinctive callouts and map gradients.

