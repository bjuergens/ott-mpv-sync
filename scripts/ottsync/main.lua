-- ottsync: mpv plugin that follows an OpenTogetherTube room (passive viewer).
--
-- PHASE 1 (current): scaffolding only. This is a dummy that proves the script
-- loads, runs on mpv's event loop, and can log. It prints a heartbeat with a
-- timestamp roughly once per second. No network, no playback control yet.
--
-- The real follower (WebSocket -> OTT room -> drive loadfile/seek/pause) lands
-- in a later phase; see research/ for the protocol reference.

local mp = require("mp")
local msg = require("mp.msg")

local NAME = "ottsync"
local TICK_INTERVAL = 1.0 -- seconds

local tick_count = 0

-- Format the current wall-clock time as HH:MM:SS. mpv's Lua is LuaJIT, so
-- os.date is available.
local function timestamp()
    return os.date("%Y-%m-%dT%H:%M:%S")
end

local function on_tick()
    tick_count = tick_count + 1
    -- msg.info prints at the "info" log level, which shows in the terminal and
    -- in mpv's log file. We also print the elapsed playback position when a
    -- file is loaded, as an early sanity check that we can read player state.
    local pos = mp.get_property_number("time-pos")
    local pos_str = pos and string.format("%.2fs", pos) or "no file"
    msg.info(string.format("heartbeat #%d @ %s (time-pos: %s)",
        tick_count, timestamp(), pos_str))
end

local function main()
    msg.info(string.format("%s loaded (phase 1: dummy heartbeat)", NAME))
    -- add_periodic_timer fires on mpv's event loop every TICK_INTERVAL seconds.
    mp.add_periodic_timer(TICK_INTERVAL, on_tick)
end

main()
