module Config

using TOML

export load_vault_path

function load_vault_path()
    # lookup order: env override, then dev default
	cfgpath = get(
    ENV,
    "PF_CONFIG",
    joinpath(@__DIR__, "..", "config", "config.toml"),
	)

    cfg = TOML.parsefile(cfgpath)
    return cfg["vault_path"]::String
end

end # module
