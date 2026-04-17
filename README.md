# Case Clicker Hub

Desktop-app for å håndtere flere case-clicker.com-brukere fra én lokal kontroll­panel.

- Installeres som vanlig Windows-app med Start-meny-snarvei og ren avinstallering
- Kjører lokalt på `https://127.0.0.1:5000` — ingen ngrok, ingen public tunnel
- Tvunget auto-oppdatering via GitHub Releases

---

## For sluttbrukere (installasjon)

### 1. Last ned installeren

Hent siste `CCHub-Setup-x.y.z.exe` fra [Releases](https://github.com/Mikmail02/Alt-manager/releases/latest).

### 2. Kjør installeren

Windows SmartScreen vil advare første gang (appen er ikke signert). Klikk **"Mer info" → "Kjør likevel"**. Installeren krever ikke admin.

### 3. Første oppstart

Appen starter i systemstatusfeltet (tray). Ved første kjøring:

- Genererer et lokalt rot-sertifikat og installerer det i Windows-trust-store
- Genererer en personlig API-token for workerne
- Åpner panelet automatisk på `https://127.0.0.1:5000`

### 4. Installer Tampermonkey + workere

- Installer [Tampermonkey](https://www.tampermonkey.net/) i Chrome
- Opprett to nye Tampermonkey-skript:
  - Lim inn innholdet fra [`mainacc.js`](mainacc.js) i ett — dette er for main-brukeren
  - Lim inn innholdet fra [`altacc.js`](altacc.js) i ett — dette er for alle alts

### 5. Koble workerne til appen

1. Høyreklikk tray-ikonet → **"Kopier worker-link"**
2. Gå til en case-clicker.com-fane
3. Lim inn i URL-feltet i worker-boksen nederst til høyre, trykk **CONNECT**

Dette lagrer URL + token én gang — aldri behov for å gjøre det igjen.

### 6. Auto-oppdateringer

Neste gang en ny versjon slippes, blokkerer appen all bruk til oppdatering er installert. Ett klikk på **"Oppdater nå"** laster ned og installerer uten videre input.

---

## For utviklere

### Oppsett

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Kjør lokalt

```powershell
python main.py
```

Første kjøring genererer `%APPDATA%\CCHub\` med cert, config og datafiler.

### Prosjektstruktur

```
cchub/              Python-pakken (app-logikk)
  auth.py           Token-middleware for Flask
  cert_manager.py   Generer + installer lokal CA + server-cert
  config.py         Per-install config (token, preferanser)
  paths.py          AppData + resource path helpers
  tray.py           pystray tray-icon + update-gate
  updater.py        GitHub Releases auto-update
  version.py        __version__ + metadata
app.py              Flask-backend (route-handlere + HTML-panel)
main.py             Entry point for PyInstaller
altacc.js           Tampermonkey-skript for alt-brukere
mainacc.js          Tampermonkey-skript for main-brukeren
cases.json          Read-only case/capsule-liste
assets/icon.ico     App-ikon (genereres av tools/make_icon.py)
build.spec          PyInstaller-spec
installer/          Inno Setup-script
tools/              Hjelpeskript (icon-gen, build-release)
```

### Bygg én .exe + installer lokalt

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
```

Krever [Inno Setup 6](https://jrsoftware.org/isinfo.php) installert. Output havner i `installer\Output\CCHub-Setup-x.y.z.exe`.

### Slipp ny versjon

1. Bump `__version__` i `cchub/version.py` og `AppVersion` i `installer/CCHub.iss`
2. Commit + push
3. Tag: `git tag v1.0.1 && git push origin v1.0.1`
4. GitHub Actions bygger og publiserer installeren til Releases automatisk
5. Eksisterende brukere får tvungen oppdatering ved neste oppstart

### Feilsøking

**Cert-advarsel i browser**
Kjør appen én gang — rot-CA installeres automatisk. Hvis ikke, kjør manuelt:
```powershell
certutil -user -addstore Root "%APPDATA%\CCHub\cert\ca.cert.pem"
```

**Worker rapporterer 401**
Token mangler eller feil. Høyreklikk tray → **Kopier worker-link** → lim inn på nytt og trykk CONNECT.

**Panel åpner ikke**
Sjekk `%APPDATA%\CCHub\logs\tray.log`. Port 5000 kan være okkupert — stopp andre prosesser på den porten.
