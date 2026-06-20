module Responses

using HTTP, JSON3

export json_response, read_json_body

function json_response(data; status=200)
    return HTTP.Response(
        status,
        ["Content-Type" => "application/json; charset=utf-8"],
        JSON3.write(data),
    )
end

"""
    read_json_body(req) -> Union{Dict,Nothing}

Parse the request body as JSON. Returns `nothing` on empty/invalid body.
"""
function read_json_body(req::HTTP.Request)
    body = req.body
    (body === nothing || isempty(body)) && return nothing
    try
        return JSON3.read(IOBuffer(body), Dict{String,Any})
    catch
        return nothing
    end
end

end # module
