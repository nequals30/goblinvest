module Routes
using HTTP

using ..Responses: json_response
using ..Auth: with_session
using ..API.AccountsAPI: summarize_accounts_handler
using ..API.AuthAPI: signup_handler, login_handler, logout_handler, me_handler

export router

const PUBLIC_DIR = normpath(joinpath(@__DIR__, "..", "public"))

function serve_static(path::AbstractString)
    rel = strip(String(path), '/')
    rel = isempty(rel) ? "index.html" : rel

    full = normpath(joinpath(PUBLIC_DIR, rel))
    startswith(full, PUBLIC_DIR) || return HTTP.Response(403, "Forbidden")
    isfile(full) || return HTTP.Response(404, "Not Found")

    mime =
        endswith(full, ".html") ? "text/html; charset=utf-8" :
        endswith(full, ".js")   ? "text/javascript; charset=utf-8" :
        endswith(full, ".css")  ? "text/css; charset=utf-8" :
        endswith(full, ".json") ? "application/json; charset=utf-8" :
        "application/octet-stream"

    return HTTP.Response(200, ["Content-Type" => mime], read(full))
end

function router(req::HTTP.Request)
    try
        method = String(req.method)
        path   = HTTP.URI(req.target).path

        # Auth API — public
        if method == "POST" && path == "/api/signup"
            return signup_handler(req)
        end
        if method == "POST" && path == "/api/login"
            return login_handler(req)
        end
        if method == "POST" && path == "/api/logout"
            return logout_handler(req)
        end

        # Auth API — gated
        if method == "GET" && path == "/api/me"
            return with_session(me_handler)(req)
        end

        # App API — gated
        if method == "GET" && path == "/api/summarize_accounts"
            return with_session(summarize_accounts_handler)(req)
        end

        # Static
        if method == "GET"
            return serve_static(path)
        end

        return HTTP.Response(404, "Not Found")
    catch err
        if err isa Base.IOError && getfield(err, :code) == Base.Libc.EPIPE
            return HTTP.Response(204)
        end
        rethrow()
    end
end

end # module
