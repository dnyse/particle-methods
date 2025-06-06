from forest_fire_model.model import ForestFireModel
from forest_fire_model.particles import CellState, FireParticle
from forest_fire_model.maps import *
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
import matplotlib.gridspec as gridspec
import time
import pickle
import os
from datetime import datetime
import sys

try:
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


def create_coastal_fuel_ignitions(model, args):
    """Create better ignition points for coastal simulation that actually work"""
    ignition_points = []

    if args.map_type == "coastal":
        y_positions = [args.height // 4, args.height // 2, 3 * args.height // 4]

        for base_y in y_positions:
            coastline_distance = args.width // 4
            coast_x = coastline_distance + int(
                5 * np.sin(base_y * 0.1) + 3 * np.cos(base_y * 0.05)
            )

            # Define ignition points at different distances from coast
            potential_ignitions = [
                (coast_x + 12, base_y, f"Near Coast Y{base_y}"),  # 12 units inland
                (coast_x + 25, base_y, f"Transition Y{base_y}"),  # 25 units inland
                (coast_x + 45, base_y, f"Inland Y{base_y}"),  # 45 units inland
            ]

            for ig_x, ig_y, zone_name in potential_ignitions:
                if (
                    0 <= ig_x < args.width
                    and 0 <= ig_y < args.height
                    and model.grid[ig_x, ig_y] == CellState.FUEL.value
                ):

                    fuel_type = model.fuel_types[ig_x, ig_y]
                    moisture = model.moisture[ig_x, ig_y]

                    if fuel_type > 0.8 and moisture < 0.6:
                        ignition_points.append((ig_x, ig_y, zone_name))
                        print(
                            f"  Added ignition: {zone_name} at ({ig_x}, {ig_y}) - Fuel: {fuel_type:.2f}, Moisture: {moisture:.2f}"
                        )
                    else:
                        print(
                            f"  Skipped {zone_name} - poor conditions (Fuel: {fuel_type:.2f}, Moisture: {moisture:.2f})"
                        )

    else:
        ignition_points = [
            (args.ignite_x, args.ignite_y, "Center"),
            (args.ignite_x + 10, args.ignite_y, "East"),
            (args.ignite_x - 10, args.ignite_y, "West"),
            (args.ignite_x, args.ignite_y + 10, "North"),
            (args.ignite_x, args.ignite_y - 10, "South"),
        ]

    print(
        f"Created {len(ignition_points)} viable ignition points for coastal simulation"
    )
    return ignition_points


def create_enhanced_fuel_types(model, args):
    """Create enhanced fuel types with distinct characteristics and colors.

    This function preserves existing fuel patterns and only enhances areas
    that are still at default fuel type (1.0).
    """

    fuel_patches = [
        {"type": 2.0, "color": "brown", "name": "Dry Brush"},  # Very flammable
        {
            "type": 1.5,
            "color": "darkgreen",
            "name": "Dense Forest",
        },
        {"type": 1.0, "color": "forestgreen", "name": "Mixed Forest"},  # Normal
        {"type": 0.7, "color": "olive", "name": "Light Forest"},  # Medium
        {"type": 0.4, "color": "yellowgreen", "name": "Grassland"},  # Low flammability
    ]

    num_patches_per_type = 3

    for fuel_patch in fuel_patches:
        fuel_value = fuel_patch["type"]

        if fuel_value == 1.0:
            continue

        for _ in range(num_patches_per_type):
            center_x = np.random.randint(10, args.width - 10)
            center_y = np.random.randint(10, args.height - 10)

            patch_radius = np.random.randint(8, 20)

            for x in range(
                max(0, center_x - patch_radius),
                min(args.width, center_x + patch_radius),
            ):
                for y in range(
                    max(0, center_y - patch_radius),
                    min(args.height, center_y + patch_radius),
                ):

                    distance = np.sqrt((x - center_x) ** 2 + (y - center_y) ** 2)

                    if (
                        model.grid[x, y] == CellState.FUEL.value
                        and distance <= patch_radius
                        and abs(model.fuel_types[x, y] - 1.0) < 0.1
                    ):

                        edge_probability = max(
                            0,
                            1
                            - (distance / patch_radius)
                            + np.random.uniform(-0.3, 0.3),
                        )

                        if np.random.random() < edge_probability:
                            model.fuel_types[x, y] = fuel_value

    print("Enhanced fuel type distribution while preserving map-specific patterns")
    return fuel_patches


class SimulationDataCollector:
    """Collects and stores simulation data for analysis"""

    def __init__(self, args):
        self.args = vars(args)
        self.simulation_data = {
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "parameters": self.args,
                "map_type": args.map_type,
                "wind_direction": args.wind_direction,
                "wind_strength": args.wind_strength,
                "grid_size": (args.width, args.height),
            },
            "time_series": {
                "frame": [],
                "active_particles": [],
                "burning_cells": [],
                "burned_cells": [],
                "fuel_cells": [],
                "fire_spread_distance": [],
                "total_ignited_cells": [],
                "burn_rate": [],  # cells burned per time step
                "particle_intensity_avg": [],
                "particle_intensity_max": [],
                "wind_effect_strength": [],
            },
            "spatial_data": {
                "initial_grid": None,
                "final_grid": None,
                "fuel_types": None,
                "moisture_map": None,
                "terrain": None,
                "wind_field": None,
                "ignition_points": [],
                "burned_area_progression": [],  # Store snapshots of burned areas
            },
            "summary_stats": {},
        }

    def collect_frame_data(self, model, frame_count):
        """Collect data for current frame"""
        burning_count = np.sum(model.grid == CellState.BURNING.value)
        burned_count = np.sum(model.grid == CellState.BURNED.value)
        fuel_count = np.sum(model.grid == CellState.FUEL.value)

        if len(self.simulation_data["time_series"]["burned_cells"]) > 0:
            prev_burned = self.simulation_data["time_series"]["burned_cells"][-1]
            burn_rate = burned_count - prev_burned
        else:
            burn_rate = burned_count

        if model.particles:
            particle_intensities = [p.intensity for p in model.particles]
            avg_intensity = np.mean(particle_intensities)
            max_intensity = np.max(particle_intensities)

            particle_velocities = [np.linalg.norm(p.velocity) for p in model.particles]
            avg_velocity = np.mean(particle_velocities) if particle_velocities else 0
            max_velocity = np.max(particle_velocities) if particle_velocities else 0
        else:
            avg_intensity = 0
            max_intensity = 0
            avg_velocity = 0
            max_velocity = 0

        wind_strengths = []
        for i in range(0, model.width, 5):  # Sample every 5 cells for performance
            for j in range(0, model.height, 5):
                wind_magnitude = np.sqrt(
                    model.wind_field[i, j, 0] ** 2 + model.wind_field[i, j, 1] ** 2
                )
                wind_strengths.append(wind_magnitude)

        wind_strength_avg = np.mean(wind_strengths) if wind_strengths else 0
        wind_strength_std = np.std(wind_strengths) if wind_strengths else 0

        fire_front_cells = 0
        if burning_count > 0:
            for i in range(model.width):
                for j in range(model.height):
                    if model.grid[i, j] == CellState.BURNING.value:
                        is_front = False
                        for di in [-1, 0, 1]:
                            for dj in [-1, 0, 1]:
                                if di == 0 and dj == 0:
                                    continue
                                ni, nj = i + di, j + dj
                                if (
                                    0 <= ni < model.width
                                    and 0 <= nj < model.height
                                    and model.grid[ni, nj] == CellState.FUEL.value
                                ):
                                    is_front = True
                                    break
                            if is_front:
                                break
                        if is_front:
                            fire_front_cells += 1

        ts = self.simulation_data["time_series"]
        ts["frame"].append(frame_count)
        ts["active_particles"].append(len(model.particles))
        ts["burning_cells"].append(burning_count)
        ts["burned_cells"].append(burned_count)
        ts["fuel_cells"].append(fuel_count)
        ts["fire_spread_distance"].append(model.fire_spread_distance)
        ts["total_ignited_cells"].append(burning_count + burned_count)
        ts["burn_rate"].append(burn_rate)
        ts["particle_intensity_avg"].append(avg_intensity)
        ts["particle_intensity_max"].append(max_intensity)
        ts["wind_effect_strength"].append(wind_strength_avg)

        if "particle_velocity_avg" not in ts:
            ts["particle_velocity_avg"] = [0] * (
                len(ts["frame"]) - 1
            )  # Fill previous frames
        ts["particle_velocity_avg"].append(avg_velocity)

        if "particle_velocity_max" not in ts:
            ts["particle_velocity_max"] = [0] * (len(ts["frame"]) - 1)
        ts["particle_velocity_max"].append(max_velocity)

        if "wind_effect_std" not in ts:
            ts["wind_effect_std"] = [0] * (len(ts["frame"]) - 1)
        ts["wind_effect_std"].append(wind_strength_std)

        if "fire_front_cells" not in ts:
            ts["fire_front_cells"] = [0] * (len(ts["frame"]) - 1)
        ts["fire_front_cells"].append(fire_front_cells)

        ts["particle_velocity_avg"].append(avg_velocity)
        ts["particle_velocity_max"].append(max_velocity)
        ts["wind_effect_std"].append(wind_strength_std)
        ts["fire_front_cells"].append(fire_front_cells)

        if frame_count % 10 == 0:
            burned_mask = (model.grid == CellState.BURNED.value).astype(int)
            burning_mask = (model.grid == CellState.BURNING.value).astype(int)
            self.simulation_data["spatial_data"]["burned_area_progression"].append(
                {
                    "frame": frame_count,
                    "burned_area": burned_mask.copy(),
                    "burning_area": burning_mask.copy(),  # NEW: Also track current burning
                }
            )

    def collect_initial_data(self, model):
        """Store initial simulation state"""
        self.simulation_data["spatial_data"]["initial_grid"] = model.grid.copy()
        self.simulation_data["spatial_data"]["fuel_types"] = model.fuel_types.copy()
        self.simulation_data["spatial_data"]["moisture_map"] = model.moisture.copy()
        self.simulation_data["spatial_data"]["terrain"] = model.terrain.copy()
        self.simulation_data["spatial_data"]["wind_field"] = model.wind_field.copy()
        if model.ignition_point:
            self.simulation_data["spatial_data"]["ignition_points"].append(
                model.ignition_point
            )

    def collect_final_data(self, model):
        """Store final simulation state and calculate summary statistics"""
        self.simulation_data["spatial_data"]["final_grid"] = model.grid.copy()

        total_cells = model.width * model.height
        fuel_cells = np.sum(model.grid == CellState.FUEL.value)
        burned_cells = np.sum(model.grid == CellState.BURNED.value)
        empty_cells = np.sum(model.grid == CellState.EMPTY.value)

        ts = self.simulation_data["time_series"]

        self.simulation_data["summary_stats"] = {
            "total_simulation_time": len(ts["frame"]),
            "final_burned_percentage": (burned_cells / total_cells) * 100,
            "final_fuel_remaining": fuel_cells,
            "max_fire_spread_distance": model.fire_spread_distance,
            "max_active_particles": (
                max(ts["active_particles"]) if ts["active_particles"] else 0
            ),
            "peak_burning_cells": (
                max(ts["burning_cells"]) if ts["burning_cells"] else 0
            ),
            "average_burn_rate": np.mean(ts["burn_rate"]) if ts["burn_rate"] else 0,
            "fire_duration": len([x for x in ts["active_particles"] if x > 0]),
            "total_area_burned": burned_cells,
            "empty_area_percentage": (empty_cells / total_cells) * 100,
            "fire_intensity_peak": (
                max(ts["particle_intensity_max"]) if ts["particle_intensity_max"] else 0
            ),
            "fire_intensity_average": (
                np.mean([x for x in ts["particle_intensity_avg"] if x > 0])
                if ts["particle_intensity_avg"]
                else 0
            ),
        }

    def save_data(self, filename=None):
        """Save collected data to pickle file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            map_type = self.args["map_type"]
            filename = f"fire_simulation_{map_type}_{timestamp}.pkl"

        os.makedirs("simulation_data", exist_ok=True)
        filepath = os.path.join("simulation_data", filename)

        with open(filepath, "wb") as f:
            pickle.dump(self.simulation_data, f)

        print(f"Simulation data saved to: {filepath}")
        return filepath


def run_combined_visualization(args):
    """Combined environment and fire simulation visualization with data collection."""

    data_collector = SimulationDataCollector(args)

    model = ForestFireModel(
        args.width,
        args.height,
        spread_rate=args.spread_rate,
        random_strength=args.random_strength,
        intensity_decay=args.intensity_decay,
        min_intensity=args.min_intensity,
        ignition_probability=args.ignition_probability,
        particle_generation_rate=args.particle_generation_rate,
        initial_particles=args.initial_particles,
        burnout_rate=args.burnout_rate,
        particle_lifetime=args.particle_lifetime,
    )

    # Initialize environment
    model.initialize_random_terrain(smoothness=args.terrain_smoothness)

    # Set base moisture and fuel types BEFORE map creation
    model.set_moisture_gradient(base_moisture=args.base_moisture)
    model.fuel_types = np.ones((model.width, model.height))

    if args.variable_wind:
        model.set_variable_wind(
            base_direction=args.wind_direction,
            base_strength=args.wind_strength,
            variability=0.2,
        )
    else:
        model.set_uniform_wind(
            direction=args.wind_direction, strength=args.wind_strength
        )

    if not args.remove_barriers:
        if args.map_type == "houses":
            create_small_city(model, args)
        elif args.map_type == "river":
            create_river_map(model, args)
        elif args.map_type == "wui":
            create_urban_map(model, args)
        elif args.map_type == "coastal":
            create_coastal_map(model, args)
        elif args.map_type == "mixed":
            create_mixed_map(model, args)
        elif args.map_type == "forest":
            print("Created pure forest map (no barriers)")

        print(f"Map type: {args.map_type}")

    if args.map_type == "forest" or (
        args.fuel_types > 5 and args.map_type not in ["coastal", "wui"]
    ):
        fuel_patches = create_enhanced_fuel_types(model, args)
    else:
        fuel_patches = [
            {"type": 2.0, "color": "brown", "name": "Dry Brush"},
            {"type": 1.5, "color": "darkgreen", "name": "Dense Forest"},
            {"type": 1.0, "color": "forestgreen", "name": "Mixed Forest"},
            {"type": 0.7, "color": "olive", "name": "Light Forest"},
            {"type": 0.4, "color": "yellowgreen", "name": "Grassland"},
        ]

    def visualize_with_fuel_colors(model, ax=None, show_particles=True):
        """Enhanced visualization with fuel type colors."""
        if ax is None:
            fig, ax = plt.subplots(figsize=(10, 8))

        fuel_display = np.zeros((model.width, model.height, 3))  # RGB array

        fuel_color_map = {
            0.4: [0.6, 0.8, 0.2],  # Grassland - yellow-green
            0.7: [0.4, 0.6, 0.2],  # Light Forest - olive
            1.0: [0.1, 0.4, 0.1],  # Mixed Forest - forest green
            1.5: [0.0, 0.3, 0.0],  # Dense Forest - dark green
            2.0: [0.5, 0.3, 0.1],  # Dry Brush - brown
        }

        for i in range(model.width):
            for j in range(model.height):
                if model.grid[i, j] == CellState.FUEL.value:
                    fuel_value = model.fuel_types[i, j]
                    # Find closest fuel type
                    closest_fuel = min(
                        fuel_color_map.keys(), key=lambda x: abs(x - fuel_value)
                    )
                    fuel_display[i, j] = fuel_color_map[closest_fuel]
                elif model.grid[i, j] == CellState.BURNING.value:
                    fuel_display[i, j] = [1.0, 0.0, 0.0]  # Red
                elif model.grid[i, j] == CellState.BURNED.value:
                    fuel_display[i, j] = [0.0, 0.0, 0.0]  # Black
                elif model.grid[i, j] == CellState.EMPTY.value:
                    fuel_display[i, j] = [0.7, 0.7, 0.9]  # Light blue

        ax.imshow(
            fuel_display.transpose(1, 0, 2),
            origin="lower",
            extent=[0, model.width, 0, model.height],
        )

        if show_particles and model.particles:
            particle_x = [p.position[0] for p in model.particles]
            particle_y = [p.position[1] for p in model.particles]
            intensity = [p.intensity for p in model.particles]

            sizes = [i * 30 for i in intensity]
            ax.scatter(particle_x, particle_y, s=sizes, color="yellow", alpha=0.7)

        if show_particles:
            terrain_contour = ax.contour(
                np.arange(model.width),
                np.arange(model.height),
                model.terrain.T,
                levels=8,
                colors="white",
                alpha=0.3,
                linewidths=0.8,
            )

        burned_percent = (
            np.sum(model.grid == CellState.BURNED.value)
            / (model.width * model.height)
            * 100
        )
        burning_count = np.sum(model.grid == CellState.BURNING.value)
        fuel_left = np.sum(model.grid == CellState.FUEL.value)

        info_text = (
            f"Fire spread: {model.fire_spread_distance:.1f} units\n"
            f"Burned: {burned_percent:.1f}%\n"
            f"Active fires: {burning_count}\n"
            f"Fuel remaining: {fuel_left}"
        )

        ax.text(
            0.98,
            0.02,
            info_text,
            transform=ax.transAxes,
            fontsize=9,
            verticalalignment="bottom",
            horizontalalignment="right",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.7),
        )

        ax.grid(which="both", color="gray", linestyle="-", linewidth=0.5, alpha=0.2)
        ax.set_title(f"Fire Simulation with Particles - {args.map_type.title()}")
        ax.set_xlabel("X")
        ax.set_ylabel("Y")

        return ax

    data_collector.collect_initial_data(model)

    fig = plt.figure(figsize=(20, 8))
    gs = gridspec.GridSpec(1, 2, width_ratios=[1, 1])

    ax_3d = fig.add_subplot(gs[0, 0], projection="3d")

    ax_right = fig.add_subplot(gs[0, 1])

    x = np.arange(0, args.width, 1)
    y = np.arange(0, args.height, 1)
    X, Y = np.meshgrid(x, y)

    terrain_surf = ax_3d.plot_surface(
        X, Y, model.terrain.T, cmap="terrain", alpha=0.7, linewidth=0, antialiased=True
    )

    fig.colorbar(terrain_surf, ax=ax_3d, shrink=0.5, aspect=5, label="Elevation")

    ax_3d.set_title(f"3D Terrain - {args.map_type.title()} Map")
    ax_3d.set_xlabel("X")
    ax_3d.set_ylabel("Y")
    ax_3d.set_zlabel("Elevation")

    from matplotlib.patches import Patch

    ignition_points_list = []
    if args.multi_ignition:
        if args.map_type == "coastal":
            ignite_points = create_coastal_fuel_ignitions(model, args)
            print(
                f"Coastal multi-ignition: Creating {len(ignite_points)} ignition points across fuel zones"
            )

            for x, y, zone_name in ignite_points:
                if 0 <= x < args.width and 0 <= y < args.height:
                    if model.grid[x, y] == CellState.FUEL.value:
                        model.ignite(x, y)
                        ignition_points_list.append((x, y))
                        data_collector.simulation_data["spatial_data"][
                            "ignition_points"
                        ].append((x, y))
                        print(
                            f"  Ignited {zone_name} at ({x}, {y}) - Fuel type: {model.fuel_types[x, y]:.2f}"
                        )
                    else:
                        print(f"  Skipped {zone_name} at ({x}, {y}) - not fuel cell")
        else:
            ignite_points = [
                (args.ignite_x, args.ignite_y),
                (args.ignite_x + 10, args.ignite_y),
                (args.ignite_x - 10, args.ignite_y),
                (args.ignite_x, args.ignite_y + 10),
                (args.ignite_x, args.ignite_y - 10),
            ]
            for x, y in ignite_points:
                if 0 <= x < args.width and 0 <= y < args.height:
                    model.ignite(x, y)
                    ignition_points_list.append((x, y))
                    data_collector.simulation_data["spatial_data"][
                        "ignition_points"
                    ].append((x, y))
    else:
        model.ignite(args.ignite_x, args.ignite_y)
        ignition_points_list.append((args.ignite_x, args.ignite_y))
    model.set_ignition_points(ignition_points_list)
    print(f"Tracking fire spread from {len(ignition_points_list)} ignition points")

    fire_points_3d = None
    frame_count = 0
    last_frame_time = time.time()
    fps_history = []
    simulation_ended = False

    progress_bar = None
    show_progress = args.save and TQDM_AVAILABLE
    if show_progress:
        progress_bar = tqdm(
            total=args.frames,
            desc="Simulation Progress",
            unit="frame",
            ncols=100,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} frames [{elapsed}<{remaining}, {rate_fmt}]",
        )
    elif args.save and not TQDM_AVAILABLE:
        print("Note: Install tqdm for progress bars: pip install tqdm")

    def limit_particles(model, max_particles):
        if len(model.particles) > max_particles:
            model.particles.sort(key=lambda p: p.intensity, reverse=True)
            model.particles = model.particles[:max_particles]
            return True
        return False

    def end_simulation_and_save():
        nonlocal simulation_ended
        if simulation_ended:
            return

        simulation_ended = True
        if progress_bar:
            progress_bar.close()
        print(f"Simulation ended at time step {frame_count}")

        final_fuel = np.sum(model.grid == CellState.FUEL.value)
        final_burned = np.sum(model.grid == CellState.BURNED.value)
        final_burning = np.sum(model.grid == CellState.BURNING.value)

        print(
            f"Final state: {final_fuel} fuel cells, {final_burned} burned cells, {final_burning} burning cells"
        )
        print(f"Active particles at end: {len(model.particles)}")

        data_collector.collect_final_data(model)
        data_file = data_collector.save_data()
        print(f"Simulation data saved to: {data_file}")

        anim.event_source.stop()
        print(f"Animaiton stopped")
        sys.exit(0)

    def animate(frame):
        nonlocal fire_points_3d, frame_count, last_frame_time, fps_history

        if simulation_ended:
            return ax_3d, ax_right

        current_time = time.time()
        elapsed = current_time - last_frame_time
        fps = 1.0 / max(elapsed, 0.001)  # Avoid division by zero
        fps_history.append(fps)
        if len(fps_history) > 20:
            fps_history.pop(0)
        avg_fps = sum(fps_history) / len(fps_history)
        last_frame_time = current_time

        particles_limited = limit_particles(model, args.max_particles)

        active = model.update(dt=args.dt)
        frame_count += 1

        data_collector.collect_frame_data(model, frame_count)

        if progress_bar and show_progress:
            active_particles = len(model.particles)
            burning_cells = np.sum(model.grid == CellState.BURNING.value)
            burned_cells = np.sum(model.grid == CellState.BURNED.value)

            progress_bar.set_postfix(
                {
                    "Particles": active_particles,
                    "Burning": burning_cells,
                    "Burned": burned_cells,
                    "FPS": f"{avg_fps:.1f}",
                }
            )
            progress_bar.update(1)

        active_particles = len(model.particles)
        active_burning = np.sum(model.grid == CellState.BURNING.value)

        fire_completely_out = active_particles == 0 and active_burning == 0
        max_frames_reached = frame_count >= args.frames

        if fire_completely_out or max_frames_reached:
            if fire_completely_out:
                print(f"Fire completely extinguished at frame {frame_count}")
            else:
                print(f"Maximum frames ({args.frames}) reached")

            end_simulation_and_save()
            return ax_3d, ax_right

        ax_right.clear()
        visualize_with_fuel_colors(model, ax_right, show_particles=True)

        combined_legend_elements = [
            Patch(facecolor=[0.5, 0.3, 0.1], label="Dry Brush"),
            Patch(facecolor=[0.0, 0.3, 0.0], label="Dense Forest"),
            Patch(facecolor=[0.1, 0.4, 0.1], label="Mixed Forest"),
            Patch(facecolor=[0.4, 0.6, 0.2], label="Light Forest"),
            Patch(facecolor=[0.6, 0.8, 0.2], label="Grassland"),
            Patch(facecolor=[1.0, 0.0, 0.0], label="Burning"),
            Patch(facecolor=[0.0, 0.0, 0.0], label="Burned"),
            Patch(facecolor=[0.7, 0.7, 0.9], label="Buildings/Water"),
        ]

        ax_right.legend(
            handles=combined_legend_elements,
            loc="upper left",
            bbox_to_anchor=(0.02, 0.98),
            fontsize=8,
            framealpha=0.9,
            title="Map Elements",
            title_fontsize=9,
            ncol=2,
        )
        ax_right.get_legend().get_title().set_fontweight("bold")

        performance_text = (
            f"FPS: {avg_fps:.1f}\n"
            f"Particles: {len(model.particles)}\n"
            f"Map: {args.map_type.title()}\n"
            f"Spread Rate: {args.spread_rate}\n"
            f"Ignition Prob: {args.ignition_probability}"
        )
        ax_right.text(
            0.98,
            0.98,
            performance_text,
            transform=ax_right.transAxes,
            fontsize=9,
            verticalalignment="top",
            horizontalalignment="right",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

        if frame_count % args.skip_3d_update == 0:
            if fire_points_3d:
                fire_points_3d.remove()
                fire_points_3d = None

            if model.particles:
                display_particles = model.particles[
                    : min(len(model.particles), args.particle_display_limit)
                ]

                particle_x = [p.position[0] for p in display_particles]
                particle_y = [p.position[1] for p in display_particles]
                particle_z = [
                    model.terrain[
                        int(min(p.position[0], model.terrain.shape[0] - 1)),
                        int(min(p.position[1], model.terrain.shape[1] - 1)),
                    ]
                    + 0.1
                    for p in display_particles
                ]
                intensity = [p.intensity for p in display_particles]

                sizes = [i * 30 for i in intensity]
                colors = [(1.0, min(1.0, i * 0.5 + 0.5), 0.0) for i in intensity]

                fire_points_3d = ax_3d.scatter(
                    particle_x,
                    particle_y,
                    particle_z,
                    c=colors,
                    s=sizes,
                    marker="o",
                    alpha=0.7,
                )

            burned_indices = np.where(model.grid == CellState.BURNED.value)
            if (
                burned_indices[0].size > 0
                and frame_count % (args.skip_3d_update * 2) == 0
            ):
                sample_size = min(1000, burned_indices[0].size)
                if burned_indices[0].size > sample_size:
                    sample_indices = np.random.choice(
                        burned_indices[0].size, sample_size, replace=False
                    )
                    burned_x = burned_indices[0][sample_indices]
                    burned_y = burned_indices[1][sample_indices]
                else:
                    burned_x = burned_indices[0]
                    burned_y = burned_indices[1]

                burned_z = [
                    model.terrain[x, y] + 0.02 for x, y in zip(burned_x, burned_y)
                ]

                ax_3d.scatter(
                    burned_x,
                    burned_y,
                    burned_z,
                    c="black",
                    s=8,
                    marker="s",
                    alpha=0.6,
                    label="Burned",
                )

            ax_3d.set_title(
                f"3D {args.map_type.title()} - Time: {frame_count}, Active Fires: {len(model.particles)}"
            )

        return ax_3d, ax_right

    plt.tight_layout()

    anim = FuncAnimation(
        fig, animate, frames=args.frames, interval=args.interval, blit=False
    )

    if args.save:
        try:
            from matplotlib.animation import FFMpegWriter

            writer = FFMpegWriter(
                fps=15, metadata=dict(artist="ForestFireModel"), bitrate=1800
            )
            anim.save(args.output, writer=writer)
            print(f"Animation saved successfully to {args.output}")
        except Exception as e:
            print(f"Error saving animation: {e}")
            print("Trying alternative writer...")
            try:
                from matplotlib.animation import PillowWriter

                writer = PillowWriter(fps=10)
                gif_output = args.output.replace(".mp4", ".gif")
                anim.save(gif_output, writer=writer)
                print(f"Animation saved as GIF to {gif_output}")
            except Exception as e2:
                print(f"Error saving as GIF: {e2}")
                print("Animation will only be displayed, not saved.")

    else:
        plt.show()

    if not simulation_ended:
        print("Animation ended without proper simulation termination - saving data now")
        end_simulation_and_save()
