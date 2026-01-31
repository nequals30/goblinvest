module Responses

using HTTP, JSON3

export cors_headers, json_response

function cors_headers()
    return [
        "Access-Control-Allow-Origin" => "*",
        "Access-Control-Allow-Headers" => "Content-Type",
        "Access-Control-Allow-Methods" => "GET, POST, OPTIONS",
    ]
end

function json_response(data; status=200)
	# turns any `data` into an HTTP response
	return HTTP.Response(
		status,
		vcat(["Content-Type" => "application/json"], cors_headers()),
		JSON3.write(data)
	)
end

end # module
