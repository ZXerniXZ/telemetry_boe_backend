# MAVLink Live Map Dashboard

Dashboard frontend per la visualizzazione in tempo reale di dati MAVLink provenienti da droni/veicoli, tramite MQTT over WebSocket.

## Stack Tecnologico

- **React 18 + Vite** (TypeScript)
- **Zustand** per state management
- **react-router-dom v6** per routing
- **mqtt.js** per connessione MQTT (over WebSocket)
- **React-Leaflet + Leaflet** per la mappa
- **shadcn/ui** come UI kit minimal
- **ESLint + Prettier** già configurati
- **vite-plugin-svgr** per icone SVG
- **react-i18next** per internazionalizzazione (IT/EN)
- **PWA ready**

## Variabili .env richieste

Crea un file `.env` nella root del frontend con:

```
VITE_MQTT_HOST=<ip-o-hostname-broker>
VITE_MQTT_PORT=<porta-ws>
VITE_MQTT_TOPIC=mavlink/+/json
```

Esempio:
```
VITE_MQTT_HOST=192.168.1.100
VITE_MQTT_PORT=9001
VITE_MQTT_TOPIC=mavlink/+/json
```

## Avvio rapido

1. Installa le dipendenze:
   ```bash
   npm install
   ```
2. Avvia il frontend in sviluppo:
   ```bash
   npm run dev
   ```
3. Accedi a [http://localhost:5173](http://localhost:5173)

## Collegamento al broker MQTT Python

- Assicurati che il backend Python pubblichi i dati MAVLink su MQTT (topic: `mavlink/<IP_SOSTITUITO_DAGLI_UNDERSCORE>/json`).
- Il broker MQTT deve essere raggiungibile via WebSocket (es: porta 9001, configurazione Mosquitto: `listener 9001` + `protocol websockets`).
- Imposta i parametri nel file `.env` come sopra.

## Architettura cartelle

```
src/
  api/mqtt.ts        // connessione e subscription
  store/devices.ts   // Zustand store
  pages/
    MapPage.tsx
    DevicePage.tsx
  components/
    MapView.tsx
    DeviceCard.tsx
    DeviceTable.tsx
  App.tsx
  main.tsx
```

## Schema dati

Ogni messaggio MQTT ha schema:

```ts
interface MavMsg {
  timestamp: number;  // epoch
  type: string;       // nome del messaggio MAVLink
  data: Record<string, any>;
}
```

Coordinate GPS (in GLOBAL_POSITION_INT):
```ts
{
  lat: number; // 1e7 gradi
  lon: number; // 1e7 gradi
  alt: number; // mm
}
```

## UX & qualità
- Spinner di loading se non ci sono dati
- Banner warning se MQTT offline/disconnesso
- Aggiornamento marker mappa in tempo reale
- Lint con ESLint + Prettier
- Script npm:
  - `dev`, `build`, `preview`, `lint`

## Extra
- Pronto per internazionalizzazione (IT/EN)
- Manifest PWA per installazione rapida

---

Per dettagli su ogni file, vedi i commenti inline nel codice.
