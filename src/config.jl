module Config

using TOML

export data_dir, vaults_dir, users_db_path, legacy_vault_path, is_prod, pbkdf2_iters

const APP_ROOT = normpath(joinpath(@__DIR__, ".."))

function _cfg()
    cfgpath = get(ENV, "PF_CONFIG", joinpath(APP_ROOT, "config", "config.toml"))
    isfile(cfgpath) || return Dict{String,Any}()
    return TOML.parsefile(cfgpath)
end

data_dir()         = get(ENV, "GV_DATA_DIR", get(_cfg(), "data_dir", joinpath(APP_ROOT, "data")))
vaults_dir()       = joinpath(data_dir(), "vaults")
users_db_path()    = joinpath(data_dir(), "users.db")
legacy_vault_path() = joinpath(APP_ROOT, "PersonalFinanceVault.db")

is_prod() = lowercase(get(ENV, "GV_ENV", "dev")) == "prod"

pbkdf2_iters() = parse(Int, get(ENV, "GV_PBKDF2_ITERS", "600000"))

function ensure_dirs!()
    mkpath(data_dir())
    mkpath(vaults_dir())
    return nothing
end

end # module
