import sqlite3
import hashlib
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

class UserDatabase:
    def __init__(self, db_path: str = "users.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Inizializza il database con le tabelle necessarie"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Tabella utenti
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    full_name TEXT,
                    phone TEXT
                )
            ''')
            
            # Tabella sessioni
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            # Tabella log accessi
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS login_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT NOT NULL,
                    ip_address TEXT,
                    success BOOLEAN NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_agent TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            conn.commit()
            
            # Crea utente admin di default se non esiste
            self.create_default_admin()
    
    def create_default_admin(self):
        """Crea un utente admin di default se non esiste"""
        try:
            admin_user = self.get_user_by_email('navonifederico777@gmail.com')
            if not admin_user:
                self.create_user(
                    username='federico',
                    password='admin123',
                    email='navonifederico777@gmail.com',
                    role='admin',
                    full_name='Federico Navoni - Amministratore Sistema'
                )
                print("✅ Utente admin Federico creato di default")
        except Exception as e:
            print(f"⚠️ Errore nella creazione admin di default: {e}")
    
    def hash_password(self, password: str) -> str:
        """Crea hash della password con salt"""
        salt = os.urandom(32).hex()
        hash_obj = hashlib.sha256()
        hash_obj.update((password + salt).encode('utf-8'))
        return f"{salt}${hash_obj.hexdigest()}"
    
    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verifica la password contro l'hash salvato"""
        try:
            salt, hash_value = password_hash.split('$')
            hash_obj = hashlib.sha256()
            hash_obj.update((password + salt).encode('utf-8'))
            return hash_obj.hexdigest() == hash_value
        except:
            return False
    
    def create_user(self, username: str, password: str, email: Optional[str] = None, 
                   role: str = 'user', full_name: Optional[str] = None, 
                   phone: Optional[str] = None) -> Dict[str, Any]:
        """Crea un nuovo utente"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                password_hash = self.hash_password(password)
                
                cursor.execute('''
                    INSERT INTO users (username, email, password_hash, role, full_name, phone)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (username, email, password_hash, role, full_name, phone))
                
                user_id = cursor.lastrowid
                conn.commit()
                
                return {
                    'id': user_id,
                    'username': username,
                    'email': email,
                    'role': role,
                    'full_name': full_name,
                    'created_at': datetime.now().isoformat(),
                    'last_login': None,
                    'is_active': True,
                    'phone': phone
                }
        except sqlite3.IntegrityError:
            raise ValueError("Username o email già esistente")
        except Exception as e:
            raise Exception(f"Errore nella creazione utente: {e}")
    
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Ottiene un utente per username"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, email, password_hash, role, created_at, 
                       last_login, is_active, full_name, phone
                FROM users WHERE username = ?
            ''', (username,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'username': row[1],
                    'email': row[2],
                    'password_hash': row[3],
                    'role': row[4],
                    'created_at': row[5] if row[5] else None,
                    'last_login': row[6] if row[6] else None,
                    'is_active': bool(row[7]),
                    'full_name': row[8],
                    'phone': row[9]
                }
            return None
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Ottiene un utente per email"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, email, password_hash, role, created_at, 
                       last_login, is_active, full_name, phone
                FROM users WHERE email = ?
            ''', (email,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'username': row[1],
                    'email': row[2],
                    'password_hash': row[3],
                    'role': row[4],
                    'created_at': row[5] if row[5] else None,
                    'last_login': row[6] if row[6] else None,
                    'is_active': bool(row[7]),
                    'full_name': row[8],
                    'phone': row[9]
                }
            return None
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Autentica un utente"""
        user = self.get_user_by_username(username)
        if user and user['is_active'] and self.verify_password(password, user['password_hash']):
            # Aggiorna last_login
            self.update_last_login(user['id'])
            return user
        return None
    
    def update_last_login(self, user_id: int):
        """Aggiorna l'ultimo login dell'utente"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET last_login = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (user_id,))
            conn.commit()
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Ottiene tutti gli utenti (senza password)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, email, role, created_at, last_login, 
                       is_active, full_name, phone
                FROM users ORDER BY created_at DESC
            ''')
            
            users = []
            for row in cursor.fetchall():
                users.append({
                    'id': row[0],
                    'username': row[1],
                    'email': row[2],
                    'role': row[3],
                    'created_at': row[4] if row[4] else None,
                    'last_login': row[5] if row[5] else None,
                    'is_active': bool(row[6]),
                    'full_name': row[7],
                    'phone': row[8]
                })
            return users
    
    def update_user(self, user_id: int, **kwargs) -> bool:
        """Aggiorna i dati di un utente"""
        allowed_fields = ['email', 'role', 'full_name', 'phone', 'is_active']
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}
        
        if not update_fields:
            return False
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                set_clause = ', '.join([f"{field} = ?" for field in update_fields.keys()])
                values = list(update_fields.values()) + [user_id]
                
                cursor.execute(f'''
                    UPDATE users SET {set_clause} WHERE id = ?
                ''', values)
                
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Errore nell'aggiornamento utente: {e}")
            return False
    
    def delete_user(self, user_id: int) -> bool:
        """Elimina un utente"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            print(f"Errore nell'eliminazione utente: {e}")
            return False
    
    def log_login_attempt(self, username: str, success: bool, ip_address: Optional[str] = None, 
                         user_agent: Optional[str] = None, user_id: Optional[int] = None):
        """Registra un tentativo di login"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO login_logs (user_id, username, ip_address, success, user_agent)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, username, ip_address, success, user_agent))
                conn.commit()
        except Exception as e:
            print(f"Errore nel logging del login: {e}")

# Istanza globale del database
user_db = UserDatabase() 