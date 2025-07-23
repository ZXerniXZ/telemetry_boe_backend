# Endpoint `isgoing` - Documentazione

## Descrizione
L'endpoint `isgoing` permette di verificare se una boa ha un comando di navigazione ("vai a") attivo.

## Endpoint Disponibili

### 1. Verifica singola boa
**GET** `/isgoing/{ip}/{port}`

Verifica se una specifica boa ha un comando di navigazione attivo.

**Parametri:**
- `ip` (string): Indirizzo IP della boa
- `port` (integer): Porta della boa

**Risposta:**
```json
{
  "isgoing": true,
  "boa": "10.8.0.30:14550",
  "destination": {
    "lat": 45.123456,
    "lon": 12.345678,
    "alt": 10.0
  },
  "started_at": 1705312200.123,
  "elapsed_time": 45.67,
  "status": "active"
}
```

**Esempi di utilizzo:**
```bash
# Verifica se la boa 10.8.0.30:14550 ha un vaia attivo
curl http://localhost:8000/isgoing/10.8.0.30/14550
```

### 2. Verifica tutte le boe
**GET** `/isgoing`

Verifica lo stato di navigazione di tutte le boe attive.

**Risposta:**
```json
{
  "boes": {
    "10.8.0.30:14550": {
      "isgoing": true,
      "destination": {
        "lat": 45.123456,
        "lon": 12.345678,
        "alt": 10.0
      },
      "started_at": 1705312200.123,
      "elapsed_time": 45.67,
      "status": "active"
    },
    "10.8.0.31:14550": {
      "isgoing": false
    },
    "10.8.0.32:14550": {
      "isgoing": false
    }
  }
}
```

**Esempi di utilizzo:**
```bash
# Verifica lo stato di tutte le boe
curl http://localhost:8000/isgoing
```

## Utilizzo nel Frontend

### JavaScript/TypeScript

```javascript
// Verifica singola boa
async function checkBoaStatus(ip, port) {
  try {
    const response = await fetch(`http://localhost:8000/isgoing/${ip}/${port}`);
    const data = await response.json();
    return data.isgoing;
  } catch (error) {
    console.error('Errore nel controllo stato boa:', error);
    return false;
  }
}

// Verifica tutte le boe
async function checkAllBoesStatus() {
  try {
    const response = await fetch('http://localhost:8000/isgoing');
    const data = await response.json();
    return data.boes;
  } catch (error) {
    console.error('Errore nel controllo stato boe:', error);
    return {};
  }
}

// Esempio di utilizzo
const isGoing = await checkBoaStatus('10.8.0.30', 14550);
console.log(`La boa sta navigando: ${isGoing}`);

const allBoesStatus = await checkAllBoesStatus();
console.log('Stato di tutte le boe:', allBoesStatus);
```

### React Hook

```javascript
import { useState, useEffect } from 'react';

function useBoaStatus(ip, port) {
  const [isGoing, setIsGoing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        setLoading(true);
        const response = await fetch(`http://localhost:8000/isgoing/${ip}/${port}`);
        const data = await response.json();
        setIsGoing(data.isgoing);
        setError(null);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };

    checkStatus();
    
    // Aggiorna ogni 5 secondi
    const interval = setInterval(checkStatus, 5000);
    return () => clearInterval(interval);
  }, [ip, port]);

  return { isGoing, loading, error };
}

// Utilizzo nel componente
function BoaStatusComponent({ ip, port }) {
  const { isGoing, loading, error } = useBoaStatus(ip, port);

  if (loading) return <div>Caricamento...</div>;
  if (error) return <div>Errore: {error}</div>;

  return (
    <div>
      <h3>Boa {ip}:{port}</h3>
      <p>Stato navigazione: {isGoing ? '🟢 Attiva' : '🔴 Ferma'}</p>
    </div>
  );
}
```

### Vue.js

```javascript
// Composables/useBoaStatus.js
import { ref, onMounted, onUnmounted } from 'vue';

export function useBoaStatus(ip, port) {
  const isGoing = ref(false);
  const loading = ref(true);
  const error = ref(null);

  const checkStatus = async () => {
    try {
      loading.value = true;
      const response = await fetch(`http://localhost:8000/isgoing/${ip}/${port}`);
      const data = await response.json();
      isGoing.value = data.isgoing;
      error.value = null;
    } catch (err) {
      error.value = err.message;
    } finally {
      loading.value = false;
    }
  };

  let interval;

  onMounted(() => {
    checkStatus();
    interval = setInterval(checkStatus, 5000);
  });

  onUnmounted(() => {
    if (interval) clearInterval(interval);
  });

  return { isGoing, loading, error };
}
```

## Note Tecniche

1. **Thread Safety**: L'endpoint utilizza lock per garantire la thread safety durante l'accesso ai dati condivisi.

2. **Pulizia Automatica**: Se un thread di navigazione è terminato, viene automaticamente rimosso dall'elenco delle attività attive.

3. **Timeout**: I comandi di navigazione hanno un timeout predefinito (vedi `VAI_A_TIMEOUT_SEC` in `shared.py`).

4. **CORS**: L'endpoint supporta CORS per richieste dal frontend.

## Stati di Risposta

### Boa con navigazione attiva:
- `isgoing: true`: La boa ha un comando di navigazione attivo e il thread è in esecuzione
- `destination`: Coordinate del punto di destinazione (lat, lon, alt)
- `started_at`: Timestamp di quando è iniziato il comando
- `elapsed_time`: Tempo trascorso dall'inizio del comando (in secondi)
- `status`: Stato del comando (sempre "active" per comandi attivi)

### Boa senza navigazione:
- `isgoing: false`: La boa non ha comandi di navigazione attivi

## Errori Comuni

- **404**: Boa non trovata o non attiva
- **500**: Errore interno del server
- **Connection Error**: Problemi di connessione al backend 