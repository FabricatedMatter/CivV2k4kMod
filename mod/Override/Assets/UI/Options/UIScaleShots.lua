----------------------------------------------------------------
-- Civ V UI Scale: screenshot automation (driven by tools/shots.py)
-- Included from the patched options menu (native/src/optionspatch.cpp, base or EUI) only
-- when UIScale.ini has Shots=<run>.
-- Each UIScale.Shot(name) logs "SHOT <name>"; the host script captures the screen then.
--   front end: waits for the user to click Continue on the legal screen, locks the mouse
--              (UIScale.LockMouse), main menu shot, then loads ShotSave
--   in game:   closes the load screen, points the camera at ShotLook (if set), then
--     1. per scale: map, pause menu, options (UI Scale slider)   -- camera untouched
--     2. per scale: city screen, tech tree, social policies      -- these move the camera
--   and exits the game. The vanilla run (Vanilla=1) takes the same shots once.
----------------------------------------------------------------
local g_run, g_save, g_scales, g_look = UIScale.ShotConfig();
if not g_run then return; end
local g_vanilla = (string.sub(g_run, 1, 7) == "vanilla");   -- vanilla, vanilla1080: Vanilla=1
local g_ctx = ContextPtr:GetID();
print("[UIScaleShots] " .. tostring(g_ctx) .. " run=" .. g_run .. " save=" .. tostring(g_save));

local g_co = nil;
local function Wait(sec)
	local t = 0;
	repeat t = t + (coroutine.yield() or 0.016); until t >= sec;
end

local function Run(fn)
	g_co = coroutine.create(fn);
	Events.LocalMachineAppUpdate.Add(function(tick, dt)
		if g_co and coroutine.status(g_co) ~= "dead" then
			local ok, err = coroutine.resume(g_co, dt);
			if not ok then
				print("[UIScaleShots] error: " .. tostring(err));
				UIScale.UnlockMouse();
				UIScale.ShotNote("ERROR: driver failed: " .. tostring(err));
				g_co = nil;
			end
		end
	end);
end

local function Shot(name)
	Wait(0.5);
	UIScale.Shot(name);
	Wait(1.5);   -- host captures in this window
end

local VK_ESCAPE = 27;

local function ScaleLabel(v)
	return v <= 0 and "auto" or string.format("s%d", math.floor(v * 100 + 0.5));
end

if g_ctx == "OptionsMenu_FrontEnd" then
	local function Visible(path)
		local c = ContextPtr:LookUpControl(path);
		return c ~= nil and not c:IsHidden();
	end

	Run(function()
		Wait(2);
		-- The legal screen is shown on every start. The user clicks its Continue button:
		-- a deliberate click into the window. From then on the real mouse is ignored and
		-- the game's cursor stays parked (no edge scrolling, no tooltips).
		local t = 0;
		if Visible("/FrontEnd/LegalScreen") then
			UIScale.ShotNote("CLICK 'Continue' in the game window, then move the mouse away");
			while Visible("/FrontEnd/LegalScreen") and t < 600 do Wait(0.25); t = t + 0.25; end
		end
		t = 0;
		while not Visible("/FrontEnd/MainMenu") and t < 60 do Wait(0.25); t = t + 0.25; end
		UIScale.LockMouse();
		UIScale.ShotNote("mouse locked, hands off until the game exits");
		Wait(4);
		-- the front end runs at UIScale.ini's Scale (shots.py: the largest ShotScales value)
		Shot(g_vanilla and (g_run .. "_mainmenu") or (g_run .. "_" .. ScaleLabel(UIScale.GetSetting()) .. "_mainmenu"));
		print("[UIScaleShots] loading " .. tostring(g_save));
		PreGame.SetLoadFileName(g_save);
		Events.PlayerChoseToLoadGame(g_save);
		-- still on the main menu after a while: the save did not load
		Wait(30);
		if Visible("/FrontEnd/MainMenu") then
			UIScale.UnlockMouse();
			UIScale.ShotNote("ERROR: the save did not load: " .. tostring(g_save));
		end
	end);

elseif g_ctx == "OptionsMenu_InGame" then
	local g_ready = false;
	Events.SequenceGameInitComplete.Add(function() g_ready = true; end);

	-- ShotLook: "x,y" plot or a city name (any player); empty = camera as loaded
	local function LookTarget()
		if not g_look or g_look == "" then return nil; end
		local x, y = string.match(g_look, "^%s*(%d+)%s*,%s*(%d+)%s*$");
		if x then return Map.GetPlot(tonumber(x), tonumber(y)); end
		local want = string.lower(g_look);
		for i = 0, GameDefines.MAX_PLAYERS - 1 do
			local p = Players[i];
			if p and p:IsEverAlive() then
				for city in p:Cities() do
					if string.lower(Locale.ConvertTextKey(city:GetNameKey())) == want or
					   string.lower(city:GetName()) == want then
						return city:Plot();
					end
				end
			end
		end
		print("[UIScaleShots] ShotLook not found: " .. g_look);
		return nil;
	end

	local function Passes()
		if g_vanilla then return { { tag = g_run } }; end
		local list = {};
		for s in string.gmatch(g_scales, "[^,%s]+") do
			local v = tonumber(s) or 0;
			table.insert(list, { scale = v, tag = g_run .. "_" .. ScaleLabel(v) });
		end
		return list;
	end

	local function SetScale(pass)
		if pass.scale and math.abs(UIScale.GetSetting() - pass.scale) > 0.001 then
			UIScale.Set(pass.scale);
			Wait(3);
		end
	end

	local function GameMenu()
		local menu = ContextPtr:LookUpControl("/InGame/GameMenu");
		if not menu then return nil; end
		UIManager:QueuePopup(menu, PopupPriority.InGameMenu);
		return menu;
	end

	Run(function()
		local waited = 0;
		while not g_ready and waited < 180 do Wait(0.5); waited = waited + 0.5; end
		Wait(2);
		-- what the load screen's "Continue" button does
		Events.LoadScreenClose();
		Game.SetPausePlayer(-1);
		UI.SetDontShowPopups(false);
		Wait(4);
		local target = LookTarget();
		if target then
			UI.LookAt(target, 0);
			Wait(1);
			local t = 0;
			while UI.IsCameraMoving() and t < 10 do Wait(0.25); t = t + 0.25; end
		end
		Wait(6);   -- fade-in, map streaming

		local passes = Passes();
		-- 1. screens that leave the camera alone
		for _, pass in ipairs(passes) do
			SetScale(pass);
			Shot(pass.tag .. "_map");

			local menu = GameMenu();
			if menu then
				Wait(2);
				Shot(pass.tag .. "_pausemenu");
				UIManager:DequeuePopup(menu);
				Wait(1.5);
			end

			if not g_vanilla and OnCategory then
				-- this context is the options menu: show its Interface tab (UI Scale slider)
				UIManager:QueuePopup(ContextPtr, PopupPriority.OptionsMenu);
				Wait(1);
				OnCategory(2);
				Wait(1.5);
				Shot(pass.tag .. "_options");
				UIScale.PostKey(VK_ESCAPE);
				Wait(2);
			end
		end

		-- 2. screens that move the camera
		local player = Players[Game.GetActivePlayer()];
		local city = player and player:GetCapitalCity();
		for _, pass in ipairs(passes) do
			SetScale(pass);
			if city then
				UI.DoSelectCityAtPlot(city:Plot());
				Wait(3);
				Shot(pass.tag .. "_city");
				Events.SerialEventExitCityScreen();
				Wait(2);
			end

			Events.SerialEventGameMessagePopup({ Type = ButtonPopupTypes.BUTTONPOPUP_TECH_TREE, Data2 = -1 });
			Wait(3);
			Shot(pass.tag .. "_techtree");
			UIScale.PostKey(VK_ESCAPE);
			Wait(2);

			Events.SerialEventGameMessagePopup({ Type = ButtonPopupTypes.BUTTONPOPUP_CHOOSEPOLICY, Data1 = 1 });
			Wait(3);
			Shot(pass.tag .. "_policies");
			UIScale.PostKey(VK_ESCAPE);
			Wait(2);
		end

		UIScale.ShotsDone();
		Wait(1);
		UI.ExitGame();
	end);
end
