module Auth

using HTTP, Nettle, Random, Base64
using ..Config: is_prod, pbkdf2_iters
using ..DB
using ..Responses: json_response

export hash_password, verify_password,
       gen_token, issue_session, revoke_session,
       with_session, set_session_cookie!, clear_session_cookie!,
       session_token, SESSION_COOKIE, SESSION_TTL

const SESSION_COOKIE = "gv_session"
const SESSION_TTL    = 60 * 60 * 24 * 30   # 30 days
const _DKLEN         = 32                  # output bytes from PBKDF2
const _HLEN          = 32                  # SHA-256 digest size

# --- base64url helpers (no padding) ----------------------------------------

_b64url(bytes::AbstractVector{UInt8}) = replace(base64encode(bytes), '+'=>'-', '/'=>'_', "="=>"")

function _b64url_decode(s::AbstractString)
    pad = mod(-length(s), 4)
    return base64decode(replace(s, '-'=>'+', '_'=>'/') * "="^pad)
end

# --- constant-time compare --------------------------------------------------

function _ct_eq(a::AbstractVector{UInt8}, b::AbstractVector{UInt8})
    length(a) != length(b) && return false
    acc = UInt8(0)
    @inbounds for i in eachindex(a)
        acc |= a[i] ⊻ b[i]
    end
    return acc == 0x00
end

# --- PBKDF2-HMAC-SHA256 -----------------------------------------------------

function _pbkdf2_sha256(password::AbstractVector{UInt8}, salt::AbstractVector{UInt8},
                       iters::Integer, dklen::Integer)
    blocks = cld(dklen, _HLEN)
    out = Vector{UInt8}(undef, blocks * _HLEN)
    for i in 1:blocks
        i32 = UInt8[(i >> 24) & 0xff, (i >> 16) & 0xff, (i >> 8) & 0xff, i & 0xff]
        salt_block = vcat(collect(salt), i32)
        U = Nettle.digest("sha256", password, salt_block)
        T = copy(U)
        @inbounds for _ in 2:iters
            U = Nettle.digest("sha256", password, U)
            for j in eachindex(T)
                T[j] ⊻= U[j]
            end
        end
        @inbounds for j in 1:_HLEN
            out[(i-1)*_HLEN + j] = T[j]
        end
    end
    return out[1:dklen]
end

function hash_password(password::AbstractString; iters::Integer = pbkdf2_iters())
    salt = rand(RandomDevice(), UInt8, 16)
    dk = _pbkdf2_sha256(Vector{UInt8}(password), salt, iters, _DKLEN)
    return "pbkdf2-sha256\$$(iters)\$$(_b64url(salt))\$$(_b64url(dk))"
end

function verify_password(password::AbstractString, stored::AbstractString)::Bool
    parts = split(stored, '$')
    length(parts) == 4 || return false
    parts[1] == "pbkdf2-sha256" || return false
    iters = tryparse(Int, parts[2]); iters === nothing && return false
    salt = try _b64url_decode(parts[3]) catch; return false end
    expected = try _b64url_decode(parts[4]) catch; return false end
    actual = _pbkdf2_sha256(Vector{UInt8}(password), salt, iters, length(expected))
    return _ct_eq(actual, expected)
end

# --- session tokens ---------------------------------------------------------

gen_token() = _b64url(rand(RandomDevice(), UInt8, 32))

function issue_session(user_id::Integer; ttl::Integer = SESSION_TTL)
    tok = gen_token()
    DB.insert_session(tok, user_id, ttl)
    return tok
end

function revoke_session(token::AbstractString)
    DB.delete_session(token)
end

# --- cookies ----------------------------------------------------------------

function session_token(req::HTTP.Request)::Union{String,Nothing}
    for c in HTTP.cookies(req)
        c.name == SESSION_COOKIE && return c.value
    end
    return nothing
end

function set_session_cookie!(resp::HTTP.Response, token::AbstractString;
                             maxage::Integer = SESSION_TTL)
    c = HTTP.Cookies.Cookie(SESSION_COOKIE, String(token);
        path = "/",
        httponly = true,
        secure = is_prod(),
        samesite = HTTP.Cookies.SameSiteLaxMode,
        maxage = Int(maxage))
    HTTP.Cookies.addcookie!(resp, c)
    return resp
end

function clear_session_cookie!(resp::HTTP.Response)
    c = HTTP.Cookies.Cookie(SESSION_COOKIE, "";
        path = "/",
        httponly = true,
        secure = is_prod(),
        samesite = HTTP.Cookies.SameSiteLaxMode,
        maxage = -1)
    HTTP.Cookies.addcookie!(resp, c)
    return resp
end

# --- middleware -------------------------------------------------------------

"""
    with_session(handler)

Wrap a handler `(req, user) -> HTTP.Response`. If the request lacks a valid
session cookie, returns a 401 JSON response.
"""
function with_session(handler)
    return function (req::HTTP.Request)
        tok = session_token(req)
        user = tok === nothing ? nothing : DB.find_user_by_token(tok)
        if user === nothing
            return json_response(Dict("error" => "unauthorized"); status = 401)
        end
        return handler(req, user)
    end
end

end # module
