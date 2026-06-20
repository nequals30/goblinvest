module DB

using SQLite, DBInterface, Dates
using ..Config: users_db_path

export User, Session, init!, conn,
       insert_user, find_user_by_username, find_user_by_id, set_vault_dir!,
       insert_session, find_user_by_token, delete_session, delete_expired_sessions,
       user_count

struct User
    id::Int
    username::String
    password_hash::String
    vault_dir::String
end

struct Session
    token::String
    user_id::Int
    expires_at::String
end

const _DB = Ref{Union{SQLite.DB,Nothing}}(nothing)

function conn()
    db = _DB[]
    db === nothing && error("DB not initialized — call DB.init!() first")
    return db
end

function init!()
    path = users_db_path()
    db = SQLite.DB(path)
    _DB[] = db

    SQLite.execute(db, "PRAGMA foreign_keys = ON;")
    SQLite.execute(db, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            vault_dir TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    SQLite.execute(db, """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );""")
    SQLite.execute(db, "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);")

    delete_expired_sessions()
    return db
end

_first_row(it) = (r = iterate(it); r === nothing ? nothing : r[1])

function _user_from_row(row)
    return User(
        Int(row.id),
        String(row.username),
        String(row.password_hash),
        String(row.vault_dir),
    )
end

function user_count()::Int
    row = _first_row(DBInterface.execute(conn(), "SELECT COUNT(*) AS n FROM users"))
    return row === nothing ? 0 : Int(row.n)
end

function insert_user(username::AbstractString, password_hash::AbstractString, vault_dir::AbstractString)
    DBInterface.execute(conn(),
        "INSERT INTO users(username, password_hash, vault_dir) VALUES (?, ?, ?)",
        (username, password_hash, vault_dir))
    return Int(SQLite.last_insert_rowid(conn()))
end

function set_vault_dir!(user_id::Integer, vault_dir::AbstractString)
    DBInterface.execute(conn(),
        "UPDATE users SET vault_dir = ? WHERE id = ?",
        (vault_dir, user_id))
    return nothing
end

function find_user_by_username(username::AbstractString)::Union{User,Nothing}
    it = DBInterface.execute(conn(),
        "SELECT id, username, password_hash, vault_dir FROM users WHERE username = ?",
        (username,))
    row = _first_row(it)
    return row === nothing ? nothing : _user_from_row(row)
end

function find_user_by_id(user_id::Integer)::Union{User,Nothing}
    it = DBInterface.execute(conn(),
        "SELECT id, username, password_hash, vault_dir FROM users WHERE id = ?",
        (user_id,))
    row = _first_row(it)
    return row === nothing ? nothing : _user_from_row(row)
end

function insert_session(token::AbstractString, user_id::Integer, ttl_seconds::Integer)
    DBInterface.execute(conn(),
        "INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, datetime('now', ?))",
        (token, user_id, "+$(Int(ttl_seconds)) seconds"))
    return nothing
end

function find_user_by_token(token::AbstractString)::Union{User,Nothing}
    it = DBInterface.execute(conn(), """
        SELECT u.id, u.username, u.password_hash, u.vault_dir
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > datetime('now')
        """, (token,))
    row = _first_row(it)
    return row === nothing ? nothing : _user_from_row(row)
end

function delete_session(token::AbstractString)
    DBInterface.execute(conn(), "DELETE FROM sessions WHERE token = ?", (token,))
    return nothing
end

function delete_expired_sessions()
    DBInterface.execute(conn(), "DELETE FROM sessions WHERE expires_at <= datetime('now')")
    return nothing
end

end # module
