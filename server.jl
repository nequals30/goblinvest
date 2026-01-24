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
				"""
				<h1>Hello</h1>
				<link href="https://unpkg.com/tabulator-tables@6.2.5/dist/css/tabulator.min.css" rel="stylesheet">
				<div id="accounts-table"></div>

				<script src="https://unpkg.com/tabulator-tables@6.2.5/dist/js/tabulator.min.js"></script>
				<script src="/app.js"></script>
				"""
			)

		elseif method == "GET" && path=="/app.js"
			    js = raw"""
    async function loadAccounts() {
      const res = await fetch("/api/summarize_accounts");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();

      new Tabulator("#accounts-table", {
        layout: "fitColumns",
        data: data,
        columns: [
          { title: "Account", field: "account_name" },
          { title: "Asset", field: "asset_name" },
          { title: "Market Value", field: "market_value", hozAlign: "right", formatter: "money" },
        ],
      });
    }

    loadAccounts().catch(err => {
      console.error(err);
      document.querySelector("#accounts-table").innerText = "Failed to load accounts.";
    });
    """
    return HTTP.Response(200, ["Content-Type" => "text/javascript; charset=utf-8"], js)

		elseif method == "GET" && path=="/api/summarize_accounts"
			v = vault(load_vault_path())
			a = summarize_accounts(v)
			rows = [(account_name = n, asset_name = a, market_value = v) for (n, a, v) in zip(a.account_name, a.asset_name, a.market_value)]
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

