module AccountsAPI

using PersonalFinance, DBInterface
using ...DB
using ...Responses: json_response

export summarize_accounts_handler

# `PersonalFinance.summarize_accounts` errors on a fresh/empty vault
# (operates on Missing-typed columns from a zero-row SELECT). We pre-check
# the transactions table and short-circuit to an empty array.
function _has_transactions(v)
    it = DBInterface.execute(v.db, "SELECT 1 FROM transactions LIMIT 1")
    return iterate(it) !== nothing
end

function summarize_accounts_handler(_, user::DB.User)
    v = vault(joinpath(user.vault_dir, "PersonalFinanceVault.db"))
    _has_transactions(v) || return json_response([])

    a = summarize_accounts(v)

    rows = [(account_name = n,
             asset_name = an,
             market_value = mv,
             most_recent_trans_date = (d === missing || d === nothing) ? nothing : string(d))
            for (n, an, mv, d) in zip(a.account_name, a.asset_name, a.market_value, a.most_recent_trans_date)]

    return json_response(rows)
end

end # module
