module AccountsAPI

using PersonalFinance, DBInterface, DataFrames
using ...DB
using ...Responses: json_response

export summarize_accounts_handler, accumulate_mv_handler

# `PersonalFinance.summarize_accounts` and `accumulate_mv` both error on a
# fresh/empty vault. We pre-check the transactions table.
function _has_transactions(v)
    it = DBInterface.execute(v.db, "SELECT 1 FROM transactions LIMIT 1")
    return iterate(it) !== nothing
end

_load_vault(user::DB.User) = vault(joinpath(user.vault_dir, "PersonalFinanceVault.db"))

function summarize_accounts_handler(_, user::DB.User)
    v = _load_vault(user)
    _has_transactions(v) || return json_response([])

    a = summarize_accounts(v)

    rows = [(account_name = n,
             asset_name = an,
             market_value = mv,
             most_recent_trans_date = (d === missing || d === nothing) ? nothing : string(d))
            for (n, an, mv, d) in zip(a.account_name, a.asset_name, a.market_value, a.most_recent_trans_date)]

    return json_response(rows)
end

# Coerce vault floats (Float64, missing, NaN) to JSON-friendly values.
_jval(x) = (x === missing || (x isa AbstractFloat && !isfinite(x))) ? nothing : Float64(x)

function accumulate_mv_handler(_, user::DB.User)
    v = _load_vault(user)
    _has_transactions(v) || return json_response(Dict("dates" => String[], "series" => []))

    out, allDts = accumulate_mv(v)

    dates = string.(allDts)
    cols = sort(DataFrames.names(out))   # stable stacking order
    series = map(cols) do col
        parts = split(col, "::"; limit = 2)
        account = length(parts) >= 1 ? String(parts[1]) : ""
        asset   = length(parts) >= 2 ? String(parts[2]) : ""
        values  = [_jval(x) for x in out[!, col]]
        Dict("name" => col, "account" => account, "asset" => asset, "values" => values)
    end

    return json_response(Dict("dates" => dates, "series" => series))
end

end # module
