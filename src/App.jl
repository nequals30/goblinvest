module App

using HTTP
using Logging, LoggingExtras

include("config.jl");   using .Config
include("responses.jl"); using .Responses
include("db.jl");        using .DB
include("auth.jl");      using .Auth

module API
    include("api/accounts.jl")
    include("api/auth.jl")
end

include("routes.jl")
using .Routes: router

function filtered_logger()
    ActiveFilteredLogger(current_logger()) do log
        msg = string(log.message)
        if log._module == HTTP.Servers &&
           occursin("handle_connection handler error", msg) &&
           (occursin("broken pipe", msg) || occursin("EPIPE", msg) || occursin("ECONNRESET", msg))
            return false
        end
        true
    end
end

export start_server
function start_server(; host="127.0.0.1", port=8080)
    Config.ensure_dirs!()
    DB.init!()
    println("Starting server on http://$host:$port …")
    with_logger(filtered_logger()) do
        HTTP.serve(router, host, port; verbose=false)
    end
end

end # module App
