--[[
  Datamosh for Fusion - shared runner (used by the Datamosh macro and the Datamosher script)

  M.run(comp, opts) -> output_path | nil, error_message
    opts.source        string  video file path (optional; auto-detected if empty)
    opts.effect        number  0 auto melt, 1 melt at frame, 2 smear/bloom, 3 stutter
    opts.start         number  frame where the effect starts
    opts.melt_length   number  auto melt / melt at frame: frames the melt lasts (0 = until the end)
    opts.block_length  number  stutter: frames in the repeated block
    opts.repeats       number  smear / stutter: number of repetitions
    opts.scene         number  auto melt: scene-cut sensitivity (lower = more cuts)
    opts.jump          number  fake cut: frames skipped at the start frame (0 = off)
    opts.audio         number  0 silent, 1 original (synced), 2 pitch, 3 glitch, 4 pitch + glitch
    opts.pitch         number  pitch shift in semitones
    opts.prores        boolean ProRes (.mov) instead of H.264 (.mp4)
]]

local M = {}
local IS_WIN = package.config:sub(1, 1) == "\\"

function M.install_dir()
    if IS_WIN then
        return os.getenv("APPDATA") .. "\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\DatamoshFusion\\"
    end
    local home = os.getenv("HOME") or ""
    local mac = home .. "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/DatamoshFusion/"
    local f = io.open(mac .. "config.txt", "r")
    if f then f:close() return mac end
    return home .. "/.local/share/DaVinciResolve/Fusion/DatamoshFusion/"
end

local function read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return s
end

local function file_exists(path)
    local f = io.open(path, "rb")
    if f then f:close() return true end
    return false
end

local function clip_of(tool)
    local ok, a = pcall(function() return tool:GetAttrs() end)
    if not ok or not a then return nil end
    local id = a.TOOLS_RegID
    if id ~= "Loader" and id ~= "MediaIn" then return nil end
    if (a.TOOLS_Name or ""):match("^DatamoshResult") then return nil end
    local c = a.TOOLST_Clip_Name and a.TOOLST_Clip_Name[1]
    if c and c ~= "" then return c end
    if id == "MediaIn" then
        local ok2, p = pcall(function() return tool:GetInput("MediaProps") end)
        if ok2 and type(p) == "string" and p ~= "" then return p end
    end
    return nil
end

-- File path of the clip under the playhead, asked to Resolve itself.
-- (A MediaIn node does not expose its file path, but the timeline item does.)
local function resolve_clip_path()
    local ok, path = pcall(function()
        local res
        if fusion and fusion.GetResolve then res = fusion:GetResolve() end
        if not res and fu and fu.GetResolve then res = fu:GetResolve() end
        if not res and bmd and bmd.scriptapp then res = bmd.scriptapp("Resolve") end
        if not res then return nil end
        local tl = res:GetProjectManager():GetCurrentProject():GetCurrentTimeline()
        local item = tl:GetCurrentVideoItem()
        local mpi = item:GetMediaPoolItem()
        return mpi:GetClipProperty("File Path")
    end)
    if ok and path and path ~= "" then return path end
    return nil
end

-- Automatic source: selected Loader/MediaIn, any Loader/MediaIn in the comp,
-- then the clip under the playhead in the Resolve timeline.
function M.find_source(comp)
    for _, t in ipairs(comp:GetToolList(true) or {}) do
        local c = clip_of(t)
        if c then return c end
    end
    for _, t in ipairs(comp:GetToolList(false) or {}) do
        local c = clip_of(t)
        if c then return c end
    end
    -- MediaIn present but without a readable path: ask Resolve
    local c = resolve_clip_path()
    if c then return c end
    return nil
end

function M.run(comp, opts)
    local dir = M.install_dir()
    local core = dir .. "datamosh_core.py"
    local cfg = read_file(dir .. "config.txt")
    if not cfg or not file_exists(core) then
        return nil, "Datamosh is not installed correctly. Run the installer again. Expected folder: " .. dir
    end
    local lines = {}
    for line in cfg:gmatch("[^\r\n]+") do lines[#lines + 1] = line end
    local python, ffmpeg = lines[1], lines[2]
    if not python or python == "" then
        return nil, "config.txt has no Python path. Run the installer again."
    end

    local clip = opts.source
    if not clip or clip == "" then clip = M.find_source(comp) end
    if not clip or clip == "" then
        return nil, "No source video found. Add a MediaIn/Loader to the comp (or put the playhead over the clip)."
    end
    if not file_exists(clip) then
        return nil, "Source file not found: " .. clip
    end
    if clip:match("%.[Dd][Pp][Xx]$") or clip:match("%.[Pp][Nn][Gg]$") or clip:match("%.[Ee][Xx][Rr]$") or clip:match("%.[Tt][Ii][Ff]+$") then
        return nil, "Image sequences are not supported. Use a video file (mp4, mov, avi, mkv)."
    end

    local effects = { "void", "classic", "bloom", "repeat" }
    local ev = opts.effect
    local effect
    if type(ev) == "string" and not tonumber(ev) then
        effect = ev:lower()
    else
        effect = effects[math.floor(tonumber(ev) or 0) + 1]
    end
    if not effect then effect = "void" end
    print("[Datamosh] effect input = " .. tostring(ev) .. " -> " .. effect)
    local start = math.max(1, math.floor(tonumber(opts.start) or 30))
    local melt = math.max(0, math.floor(tonumber(opts.melt_length) or 60))
    local block = math.max(1, math.floor(tonumber(opts.block_length) or 8))
    local repeats = math.max(1, math.floor(tonumber(opts.repeats) or 20))
    local scene = tonumber(opts.scene) or 0.30
    local jump = math.max(0, math.floor(tonumber(opts.jump) or 30))
    local audios = { "none", "original", "pitch", "glitch", "pitch_glitch" }
    local audio = audios[math.floor(tonumber(opts.audio) or 1) + 1] or "original"
    local pitch = tonumber(opts.pitch) or -5
    local ext = opts.prores and ".mov" or ".mp4"
    local base = clip:gsub("%.[^/\\%.]+$", "")
    local out = string.format("%s_mosh_%s_%d%s", base, effect, os.time() % 100000, ext)
    local log = dir .. "last_run.log"

    local function q(s) return '"' .. s .. '"' end
    local args = string.format(
        "--effect %s --start %d --melt-length %d --block-length %d --count %d --scene %.2f --jump %d --audio %s --pitch %g --codec %s",
        effect, start, melt, block, repeats, scene, jump, audio, pitch, opts.prores and "prores" or "h264")
    print("[Datamosh] source: " .. clip)

    if IS_WIN then
        -- Visible console window that stays open until a key is pressed.
        -- "start /wait" blocks until the window is closed, so the result exists afterwards.
        local function esc(s) return (s:gsub("%%", "%%%%")) end
        local bat = dir .. "run_datamosh.bat"
        local f = io.open(bat, "w")
        if not f then return nil, "Cannot write " .. bat end
        f:write("@echo off\r\n")
        f:write("chcp 65001 >nul\r\n")
        f:write("title Datamosh\r\n")
        if ffmpeg and ffmpeg ~= "" then
            f:write('set "DATAMOSH_FFMPEG=' .. esc(ffmpeg) .. '"\r\n')
        end
        f:write(q(esc(python)) .. " -u " .. q(esc(core)) .. " " .. q(esc(clip)) .. " " .. q(esc(out))
            .. " " .. args .. " --log " .. q(esc(log)) .. "\r\n")
        f:write("echo.\r\n")
        f:write("echo ------------------------------------------------------------\r\n")
        f:write("echo  Finished (exit code %errorlevel%). Press any key to close this window.\r\n")
        f:write("echo ------------------------------------------------------------\r\n")
        f:write("pause >nul\r\n")
        f:close()
        os.execute('start "Datamosh" /wait ' .. q(bat))
    else
        local env = ""
        if ffmpeg and ffmpeg ~= "" then env = "DATAMOSH_FFMPEG=" .. q(ffmpeg) .. " " end
        local cmd = env .. q(python) .. " -u " .. q(core) .. " " .. q(clip) .. " " .. q(out) .. " " .. args
            .. " --log " .. q(log) .. " > " .. q(dir .. "last_run_console.log") .. " 2>&1"
        print("[Datamosh] " .. cmd)
        os.execute(cmd)
        local shown = read_file(log)
        if shown then
            for line in shown:gmatch("[^\r\n]+") do print("[Datamosh] " .. line) end
        end
    end

    if not file_exists(out) then
        local text = read_file(log) or read_file(dir .. "last_run_console.log") or "(no log)"
        return nil, "Render failed. Log tail: " .. text:sub(-600)
    end
    return out
end

return M
