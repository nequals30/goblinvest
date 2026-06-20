module AuthAPI

using HTTP
using PersonalFinance: create_vault
using ...Config: vaults_dir, legacy_vault_path
using ...DB
using ...Auth
using ...Responses: json_response, read_json_body

export signup_handler, login_handler, logout_handler, me_handler

const MIN_USERNAME = 1
const MAX_USERNAME = 64
const MIN_PASSWORD = 8

_user_dto(u::DB.User) = Dict("id" => u.id, "username" => u.username)

_err(msg, status) = json_response(Dict("error" => msg); status = status)

function _validate_credentials(body)
    body === nothing && return ("missing body", nothing, nothing)
    username = get(body, "username", nothing)
    password = get(body, "password", nothing)
    (username isa AbstractString && password isa AbstractString) ||
        return ("username and password required", nothing, nothing)
    username = strip(username)
    (MIN_USERNAME <= length(username) <= MAX_USERNAME) ||
        return ("username must be 1-64 chars", nothing, nothing)
    length(password) >= MIN_PASSWORD ||
        return ("password must be at least $MIN_PASSWORD chars", nothing, nothing)
    return (nothing, String(username), String(password))
end

function _vault_dir_for(user_id::Integer)
    return joinpath(vaults_dir(), string(user_id))
end

"""
Provision the new user's vault directory.

- If this is the very first user AND a legacy `./PersonalFinanceVault.db` exists
  at the project root, move it into the new user's dir.
- Otherwise create a fresh, empty vault via `PersonalFinance.create_vault`.
"""
function _provision_vault!(user_id::Integer)
    vdir = _vault_dir_for(user_id)
    mkpath(vdir)

    legacy = legacy_vault_path()
    is_first = DB.user_count() == 1
    if is_first && isfile(legacy)
        mv(legacy, joinpath(vdir, "PersonalFinanceVault.db"); force = false)
    else
        create_vault(interactive = false, vaultPath = vdir)
    end
    return vdir
end

function signup_handler(req::HTTP.Request)
    body = read_json_body(req)
    err, username, password = _validate_credentials(body)
    err === nothing || return _err(err, 400)

    if DB.find_user_by_username(username) !== nothing
        return _err("username already taken", 409)
    end

    # Insert user with a placeholder vault_dir, then provision and update.
    # If provisioning fails we surface a 500 and the half-written row is left
    # in place — but DB.find_user_by_username will still find it and signup
    # will be idempotent for a retry only if the user picks a different name.
    pwhash = Auth.hash_password(password)
    placeholder = "__pending__:" * username
    local user_id::Int
    try
        user_id = DB.insert_user(username, pwhash, placeholder)
    catch e
        return _err("could not create user: $(sprint(showerror, e))", 500)
    end

    local vdir::String
    try
        vdir = _provision_vault!(user_id)
        DB.set_vault_dir!(user_id, vdir)
    catch e
        return _err("could not provision vault: $(sprint(showerror, e))", 500)
    end

    token = Auth.issue_session(user_id)
    user = DB.find_user_by_id(user_id)
    resp = json_response(Dict("user" => _user_dto(user)); status = 201)
    Auth.set_session_cookie!(resp, token)
    return resp
end

function login_handler(req::HTTP.Request)
    body = read_json_body(req)
    body === nothing && return _err("missing body", 400)
    username = get(body, "username", nothing)
    password = get(body, "password", nothing)
    (username isa AbstractString && password isa AbstractString) ||
        return _err("username and password required", 400)

    user = DB.find_user_by_username(strip(username))
    if user === nothing || !Auth.verify_password(password, user.password_hash)
        return _err("invalid credentials", 401)
    end

    token = Auth.issue_session(user.id)
    resp = json_response(Dict("user" => _user_dto(user)))
    Auth.set_session_cookie!(resp, token)
    return resp
end

function logout_handler(req::HTTP.Request)
    tok = Auth.session_token(req)
    tok === nothing || Auth.revoke_session(tok)
    resp = json_response(Dict("ok" => true))
    Auth.clear_session_cookie!(resp)
    return resp
end

function me_handler(_::HTTP.Request, user::DB.User)
    return json_response(Dict("user" => _user_dto(user)))
end

end # module
