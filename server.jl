include(joinpath(@__DIR__, "src", "App.jl"))
using .App

App.start_server()

