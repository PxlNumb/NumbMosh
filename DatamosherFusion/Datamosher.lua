--[[
  Datamosher (fallback script)  -  Workspace > Scripts > Datamosher
  Same engine as the Datamosh macro, with a simple dialog instead of Inspector controls.
  Uses the Loader/MediaIn of the comp (or the clip under the playhead) automatically.
]]
local M
do
    local IS_WIN = package.config:sub(1, 1) == "\\"
    local d
    if IS_WIN then
        d = os.getenv("APPDATA") .. "\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\DatamoshFusion\\"
    else
        local h = os.getenv("HOME") or ""
        d = h .. "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/DatamoshFusion/"
        local f = io.open(d .. "config.txt", "r")
        if f then f:close() else d = h .. "/.local/share/DaVinciResolve/Fusion/DatamoshFusion/" end
    end
    local ok, mod = pcall(dofile, d .. "DatamoshRun.lua")
    if not ok then
        print("[Datamosher] Not installed. Run the installer again. Folder: " .. d)
        return
    end
    M = mod
end

local function msg(text)
    print("[Datamosher] " .. text)
    pcall(function()
        comp:AskUser("Datamosher", {
            { "info", "Text", Name = "", Default = text, ReadOnly = true, Lines = 8, Wrap = true },
        })
    end)
end

local source = M.find_source(comp)
if not source then
    local pick = comp:AskUser("Datamosher - choose a video", {
        { "file", "FileBrowse", Name = "Video file", Default = "" },
    })
    if not pick or not pick.file or pick.file == "" then return end
    source = pick.file
end

local r = comp:AskUser("Datamosher", {
    { "effect", "Dropdown", Name = "Mosh Type", Default = 0, Options = {
        "Auto Melt (at scene cuts)", "Melt at Frame (remove keyframe)",
        "Smear / Bloom (repeat motion)", "Stutter (repeat frames)" } },
    { "start", "Slider", Name = "Effect Start Frame", Integer = true, Default = 30, Min = 1, Max = 600 },
    { "melt", "Slider", Name = "Melt Length (frames, 0 = until the end)", Integer = true, Default = 60, Min = 0, Max = 300 },
    { "block", "Slider", Name = "Stutter Block Length (frames)", Integer = true, Default = 8, Min = 1, Max = 120 },
    { "repeats", "Slider", Name = "Repeat Count (Smear / Stutter)", Integer = true, Default = 20, Min = 1, Max = 200 },
    { "scene", "Slider", Name = "Scene Cut Sensitivity (lower = more cuts)", Default = 0.30, Min = 0.05, Max = 0.95 },
    { "jump", "Slider", Name = "Fake Cut Jump (frames, 0 = off)", Integer = true, Default = 30, Min = 0, Max = 300 },
    { "audio", "Dropdown", Name = "Audio", Default = 1, Options = {
        "No audio", "Original audio (synced to the edit)", "Pitch shift the moshed part",
        "Glitch / bug the moshed part", "Pitch shift + glitch" } },
    { "pitch", "Slider", Name = "Pitch Shift (semitones)", Integer = true, Default = -5, Min = -12, Max = 12 },
    { "prores", "Checkbox", Name = "ProRes (.mov)", Default = 0 },
})
if not r then return end

local out, err = M.run(comp, {
    source = source, effect = r.effect, start = r.start, melt_length = r.melt,
    block_length = r.block, repeats = r.repeats, scene = r.scene, jump = r.jump,
    audio = r.audio, pitch = r.pitch, prores = (r.prores == 1 or r.prores == true),
})
if not out then msg("Error: " .. tostring(err)) return end

comp:StartUndo("Datamosher")
local ld = comp:AddTool("Loader", -32768, -32768)
ld.Clip = out
comp:EndUndo(true)
print("[Datamosher] Done: " .. out)
