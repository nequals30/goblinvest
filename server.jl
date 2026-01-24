using PersonalFinance
using HTTP
using JSON3
using TOML


function start_server(; host="127.0.0.1", port=8080)
    println("Starting server on http://$host:$port …")


	function router(req::HTTP.Request)
		# each HTTP request goes through this function

		method = String(req.method) # eg GET or POST
		uri  = HTTP.URI(req.target)
		path = uri.path

		if method == "OPTIONS"
			return HTTP.Response(204, cors_headers(); body = "")
		end

		if method == "GET" && path=="/"
			HTTP.Response(200,
				["Content-Type" => "text/html; charset=utf-8"],
				"<h1>Hello</h1>",
			)

		elseif method == "GET" && path=="/api/summarize_accounts"
			v = vault(load_vault_path())
			a = summarize_accounts(v)
			rows = [(account_name = n, market_value = v) for (n, v) in zip(a.account_name, a.market_value)]
			return json_response(rows)

		else
			return HTTP.Response(404, "Not Found")
		end
	end

    HTTP.serve(router, host, port; verbose=false)
end

function cors_headers()
    return [
        "Access-Control-Allow-Origin" => "*",
        "Access-Control-Allow-Headers" => "Content-Type",
        "Access-Control-Allow-Methods" => "GET, POST, OPTIONS",
    ]
end


function load_vault_path()
    # lookup order: env override, then dev default
    cfgpath = get(ENV, "PF_CONFIG", joinpath(@__DIR__, "config", "config.toml"))
    cfg = TOML.parsefile(cfgpath)
    return cfg["vault_path"]::String
end

function json_response(data; status=200)
	# turns any `data` into an HTTP response
	return HTTP.Response(
		status,
		vcat(["Content-Type" => "application/json"], cors_headers()),
		JSON3.write(data)
	)
end

