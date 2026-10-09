const express = require('express');
const fs = require('fs');
const path = require('path');
const QRCode = require('qrcode');
const {
  DisconnectReason,
  useMultiFileAuthState,
  makeWASocket,
} = require('@whiskeysockets/baileys');

const app = express();
const PORT = process.env.PORT || 3001;
const SESSIONS_ROOT = path.join(__dirname, 'sessions');

app.use(express.json({ limit: '1mb' }));

const GATEWAY_SECRET = process.env.WHATSAPP_GATEWAY_SECRET || 'secret-gateway-local-klasora-2024';

app.use((req, res, next) => {
  const authHeader = req.headers['authorization'];
  const secretHeader = req.headers['x-gateway-secret'];
  const providedSecret = secretHeader || (authHeader && authHeader.startsWith('Bearer ') ? authHeader.substring(7) : null);
  
  if (providedSecret !== GATEWAY_SECRET) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
});

const sessions = new Map();

function ensureRoot() {
  fs.mkdirSync(SESSIONS_ROOT, { recursive: true });
}

function sanitizeEcoleId(raw) {
  const value = String(raw || '').trim();
  if (!/^\d+$/.test(value)) {
    throw new Error('ecoleId invalide');
  }
  return value;
}

function sessionDir(ecoleId) {
  return path.join(SESSIONS_ROOT, `ecole_${ecoleId}`);
}

function getPhone(sock) {
  const id = sock && sock.user && sock.user.id ? String(sock.user.id) : '';
  return id.split(':')[0] || null;
}

function publicStatus(session) {
  if (!session) {
    return { status: 'DECONNECTE', connected: false, phone: null };
  }
  const connected = session.status === 'CONNECTE';
  return {
    status: session.status,
    connected,
    phone: connected ? getPhone(session.sock) : null,
  };
}

async function startSession(ecoleId) {
  const id = sanitizeEcoleId(ecoleId);
  const existing = sessions.get(id);
  if (existing && existing.starting) {
    return existing.starting;
  }
  if (existing && existing.sock) {
    return existing;
  }

  const session = existing || {
    ecoleId: id,
    sock: null,
    qr: null,
    qrImage: null,
    status: 'DECONNECTE',
    starting: null,
  };
  sessions.set(id, session);

  session.starting = (async () => {
    const { state, saveCreds } = await useMultiFileAuthState(sessionDir(id));
    const sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      browser: ['KLASORA', 'Chrome', '1.0.0'],
    });

    session.sock = sock;
    session.status = 'ATTENTE_SCAN';

    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', async (update) => {
      if (update.qr) {
        session.qr = update.qr;
        session.qrImage = await QRCode.toDataURL(update.qr);
        session.status = 'ATTENTE_SCAN';
      }

      if (update.connection === 'open') {
        session.status = 'CONNECTE';
        session.qr = null;
        session.qrImage = null;
      }

      if (update.connection === 'close') {
        const code = update.lastDisconnect && update.lastDisconnect.error
          && update.lastDisconnect.error.output
          && update.lastDisconnect.error.output.statusCode;
        session.sock = null;
        session.status = 'DECONNECTE';
        session.starting = null;

        if (code !== DisconnectReason.loggedOut) {
          setTimeout(() => startSession(id).catch(console.error), 3000);
        }
      }
    });

    session.starting = null;
    return session;
  })().catch((error) => {
    session.starting = null;
    session.status = 'DECONNECTE';
    throw error;
  });

  return session.starting;
}

async function getOrStartSession(ecoleId) {
  const id = sanitizeEcoleId(ecoleId);
  return sessions.get(id) || startSession(id);
}

async function purgeSession(ecoleId) {
  const id = sanitizeEcoleId(ecoleId);
  const session = sessions.get(id);
  if (session && session.sock) {
    try {
      await session.sock.logout();
    } catch (error) {
      // La suppression locale reste prioritaire si le telephone est deja hors ligne.
    }
    try {
      session.sock.end && session.sock.end();
    } catch (error) {
      // Ignore.
    }
  }
  sessions.delete(id);
  fs.rmSync(sessionDir(id), { recursive: true, force: true });
}

app.get('/session/:ecoleId/status', async (req, res) => {
  try {
    const session = await getOrStartSession(req.params.ecoleId);
    res.json(publicStatus(session));
  } catch (error) {
    res.status(400).json({ status: 'DECONNECTE', connected: false, error: error.message });
  }
});

app.get('/session/:ecoleId/qr', async (req, res) => {
  try {
    const session = await getOrStartSession(req.params.ecoleId);
    res.json({
      ...publicStatus(session),
      qrImage: session.qrImage || null,
    });
  } catch (error) {
    res.status(400).json({ status: 'DECONNECTE', connected: false, qrImage: null, error: error.message });
  }
});

app.post('/session/:ecoleId/send', async (req, res) => {
  try {
    const session = await getOrStartSession(req.params.ecoleId);
    if (!session || session.status !== 'CONNECTE' || !session.sock) {
      return res.status(409).json({
        success: false,
        status: session ? session.status : 'DECONNECTE',
        error: "WhatsApp de cette ecole n'est pas appaire.",
      });
    }

    const to = String(req.body.to || '').replace(/[^\d]/g, '');
    const text = String(req.body.text || req.body.message || '').trim();
    if (!to || !text) {
      return res.status(400).json({ success: false, error: 'Destinataire ou message manquant.' });
    }

    await session.sock.sendMessage(`${to}@s.whatsapp.net`, { text });
    return res.json({ success: true });
  } catch (error) {
    return res.status(500).json({ success: false, error: error.message });
  }
});

app.post('/session/:ecoleId/logout', async (req, res) => {
  try {
    await purgeSession(req.params.ecoleId);
    res.json({ success: true, status: 'DECONNECTE', connected: false });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

function restoreExistingSessions() {
  ensureRoot();
  for (const entry of fs.readdirSync(SESSIONS_ROOT, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith('ecole_')) continue;
    const ecoleId = entry.name.replace(/^ecole_/, '');
    if (/^\d+$/.test(ecoleId)) {
      startSession(ecoleId).catch((error) => {
        console.error(`Impossible de restaurer la session ecole ${ecoleId}:`, error.message);
      });
    }
  }
}

restoreExistingSessions();

app.listen(PORT, '127.0.0.1', () => {
  console.log(`Passerelle WhatsApp multi-ecoles ecoute sur http://127.0.0.1:${PORT}`);
});
