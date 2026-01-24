using PersonalFinance
using HTTP
using JSON3

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
		v = vault()
		a = summarize_accounts(v)
		rows = [(account_name = n, market_value = v) for (n, v) in zip(a.account_name, a.market_value)]
		return json_response(rows)

	else
		return HTTP.Response(404, "Not Found")
	end
end

function start_server(; host="127.0.0.1", port=8080)
    println("Starting server on http://$host:$port …")
    HTTP.serve(router, host, port; verbose=false)
end

function cors_headers()
    return [
        "Access-Control-Allow-Origin" => "*",
        "Access-Control-Allow-Headers" => "Content-Type",
        "Access-Control-Allow-Methods" => "GET, POST, OPTIONS",
    ]
end
