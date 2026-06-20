module AccountsAPI
using PersonalFinance
using ...Config: load_vault_path
using ...Responses: json_response

export summarize_accounts_handler

function summarize_accounts_handler(::Any)
    v = vault(load_vault_path())
    a = summarize_accounts(v)

	rows = [(account_name = n,
         asset_name = an,
         market_value = mv,
         most_recent_trans_date = (d === missing || d === nothing) ? nothing : string(d))
        for (n, an, mv, d) in zip(a.account_name, a.asset_name, a.market_value, a.most_recent_trans_date)]

    return json_response(rows)
end

end

