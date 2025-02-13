import argparse
import cv2
import numpy as np
import os
import copy
import sys
import time
import json

import open3d as o3d
import depth_map_tools
from contextlib import contextmanager
import time

@contextmanager
def timer(name = 'not named'):
    start = time.perf_counter()
    yield
    end = time.perf_counter()
    print(f"{name}: {end - start:.6f} seconds")

np.set_printoptions(suppress=True, precision=4)

def convert_to_equirectangular(image, input_fov=100):
    """
    Maps an input rectilinear image rendered at a limited FOV (e.g., 100°)
    into a 180° equirectangular image while keeping the output size the same as the input.
    The valid image (representing the central 100°) is centered,
    with black padding on the sides and top/bottom.
    
    Parameters:
      image: Input image (H x W x 3, np.uint8)
      input_fov: Field of view (in degrees) of the input image. Default is 100.
      
    Returns:
      A new image (np.uint8) of the same shape as input, representing a 180° equirectangular projection.
    """
    # Get image dimensions.
    H, W = image.shape[:2]
    # Center coordinates of the input image.
    cx = (W - 1) / 2.0
    cy = (H - 1) / 2.0
    
    # For the output, we want to cover a horizontal range of [-90, 90] degrees.
    # Create a grid of pixel coordinates for the output image.
    x_coords = np.linspace(0, W - 1, W)
    y_coords = np.linspace(0, H - 1, H)
    grid_x, grid_y = np.meshgrid(x_coords, y_coords)
    
    # Map output pixel positions to spherical angles.
    # Horizontal: x -> theta in [-pi/2, pi/2]
    theta = (grid_x - cx) / cx * (np.pi / 2)
    # Vertical: y -> phi in [-pi/2, pi/2]
    phi = (grid_y - cy) / cy * (np.pi / 2)
    
    # The input image covers only a limited field of view.
    half_input_fov = np.radians(input_fov / 2.0)  # e.g. 50° in radians.
    
    # For a pinhole model, the relationship is: u = f * tan(theta) + cx.
    # Compute the effective focal lengths (assuming symmetric FOV horizontally and vertically).
    f_x = cx / np.tan(half_input_fov)
    f_y = cy / np.tan(half_input_fov)
    
    # Create a mask: valid if the output angle is within the input's FOV.
    valid_mask = (np.abs(theta) <= half_input_fov) & (np.abs(phi) <= half_input_fov)
    
    # For valid pixels, compute the corresponding input coordinates.
    # (These equations invert the pinhole projection: theta = arctan((u-cx)/f))
    map_x = f_x * np.tan(theta) + cx
    map_y = f_y * np.tan(phi) + cy
    
    # For invalid pixels (outside the input FOV), assign dummy values.
    # We'll set them to -1 so that cv2.remap (with BORDER_CONSTANT) returns black.
    map_x[~valid_mask] = -1
    map_y[~valid_mask] = -1
    
    # Convert mapping arrays to float32 (required by cv2.remap).
    map_x = map_x.astype(np.float32)
    map_y = map_y.astype(np.float32)
    
    # Remap the image. Pixels with mapping -1 will be filled with borderValue.
    equirect_img = cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    
    return equirect_img


if __name__ == '__main__':
    
    # Setup arguments
    parser = argparse.ArgumentParser(description='Take a rgb encoded depth video and a color video, and render them it as a steroscopic 3D video.'+
        'that can be used on 3d tvs and vr headsets.')
    
    parser.add_argument('--depth_video', type=str, help='video file to use as input', required=True)
    parser.add_argument('--color_video', type=str, help='video file to use as color input', required=False)
    parser.add_argument('--xfov', type=int, help='fov in deg in the x-direction, calculated from aspectratio and yfov in not given', required=False)
    parser.add_argument('--yfov', type=int, help='fov in deg in the y-direction, calculated from aspectratio and xfov in not given', required=False)
    parser.add_argument('--max_depth', default=20, type=int, help='the max depth that the input video uses', required=False)
    parser.add_argument('--transformation_file', type=str, help='file with scene transformations from the aligner', required=False)
    parser.add_argument('--transformation_lock_frame', default=0, type=int, help='the frame that the transfomrmation will use as a base', required=False)
    parser.add_argument('--pupillary_distance', default=63, type=int, help='pupillary distance in mm', required=False)
    parser.add_argument('--max_frames', default=-1, type=int, help='quit after max_frames nr of frames', required=False)
    parser.add_argument('--touchly0', action='store_true', help='Render as touchly0 format. ie. stereo video with 3d ', required=False)
    parser.add_argument('--vr180', action='store_true', help='Render as vr180 format. ie. stereo video at 180 deg ', required=False)
    
    parser.add_argument('--touchly1', action='store_true', help='Render as touchly1 format. ie. mono video with 3d', required=False)
    parser.add_argument('--touchly_max_depth', default=5, type=float, help='the max depth that touchly is cliped to', required=False)
    parser.add_argument('--compressed', action='store_true', help='Render the video in a compressed format. Reduces file size but also quality.', required=False)
    parser.add_argument('--infill_mask', action='store_true', help='Save infill mask video.', required=False)
    parser.add_argument('--remove_edges', action='store_true', help='Tries to remove edges that was not visible in image', required=False)
    parser.add_argument('--mask_video', type=str, help='video file to use as mask input to filter out the forground and generate a background version of the mesh that can be used as infill. Requires non moving camera or very good tracking.', required=False)
    parser.add_argument('--save_background', action='store_true', help='Save the compound background as a file. To be ussed as infill.', required=False)
    parser.add_argument('--load_background', help='Load the compound background as a file. To be used as infill.', required=False)
    
    
    args = parser.parse_args()
    
    if args.xfov is None and args.yfov is None:
        print("Either --xfov or --yfov is required.")
        exit(0)
    
    
   
    MODEL_maxOUTPUT_depth = args.max_depth
    
    # Verify input file exists
    if not os.path.isfile(args.depth_video):
        raise Exception("input video does not exist")
    
    color_video = None
    if args.color_video is not None:
        if not os.path.isfile(args.color_video):
            raise Exception("input color_video does not exist")
        color_video = cv2.VideoCapture(args.color_video)
        
    mask_video = None
    if args.mask_video is not None:
        if not os.path.isfile(args.mask_video):
            raise Exception("input mask_video does not exist")
        mask_video = cv2.VideoCapture(args.mask_video)
    
    transformations = None
    if args.transformation_file is not None:
        if not os.path.isfile(args.transformation_file):
            raise Exception("input transformation_file does not exist")
        with open(args.transformation_file) as json_file_handle:
            transformations = json.load(json_file_handle)
    
        if args.transformation_lock_frame != 0:
            ref_frame = transformations[args.transformation_lock_frame]
            ref_frame_inv_trans = np.linalg.inv(ref_frame)
            for i, transformation in enumerate(transformations):
                transformations[i] = transformation @ ref_frame_inv_trans
        
    raw_video = cv2.VideoCapture(args.depth_video)
    frame_width, frame_height = int(raw_video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(raw_video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_rate = raw_video.get(cv2.CAP_PROP_FPS)
        
    cam_matrix = depth_map_tools.compute_camera_matrix(args.xfov, args.yfov, frame_width, frame_height)
    render_cam_matrix = cam_matrix
    out_width , out_height = frame_width, frame_height
    
    if args.touchly0:
        args.vr180 = True
        
    if args.vr180:
        out_width , out_height = 1920, 1920
        max_fov = max(args.xfov, args.yfov)
        if max_fov >= 180:
            raise ValueError("fov cant be 180 or over, the tool is not built to handle fisheye distorted input video")
        render_fov = max(75, max_fov)
        render_cam_matrix = depth_map_tools.compute_camera_matrix(render_fov, render_fov, out_width, out_height)
        
    out_size = None
    if args.touchly1:
        output_file = args.depth_video + "_Touchly1."
        out_size = (out_width, out_height*2)
    elif args.touchly0:
        output_file = args.depth_video + "_Touchly0."
        out_size = (out_width*3, out_height)
    else:
        output_file = args.depth_video + "_stereo."
        out_size = (out_width*2, out_height)
    
    # avc1 seams to be required for Quest 2 if linux complains use mp4v but those video files wont work on Quest 2
    # Read this to install avc1 codec from source https://swiftlane.com/blog/generating-mp4s-using-opencv-python-with-the-avc1-codec/
    # generally it is better to render without compression then Add compression at a later stage with a better compresser like FFMPEG.
    
    if args.compressed:
        output_file += "mp4"
        codec = cv2.VideoWriter_fourcc(*"avc1")
    else:
        output_file += "mkv"
        codec = cv2.VideoWriter_fourcc(*"FFV1")
    
    out = cv2.VideoWriter(output_file, codec, frame_rate, out_size)
    
    infill_mask_video = None
    if args.infill_mask:
        infill_mask_video = cv2.VideoWriter(output_file+"_infillmask.mkv", cv2.VideoWriter_fourcc(*"FFV1"), frame_rate, out_size)
    
    if mask_video is not None:
        # Create background "sphere"
        bg_cloud = o3d.geometry.PointCloud()
        bg_points = np.asarray(bg_cloud.points)
        bg_point_colors = np.asarray(bg_cloud.colors)
        
        if args.load_background:
            loaded_bg = np.load(args.load_background)
            bg_points = loaded_bg[0]
            bg_point_colors = loaded_bg[1]
    
    
    left_shift = -(args.pupillary_distance/1000)/2
    right_shift = +(args.pupillary_distance/1000)/2

    frame_n = 0
    last_mesh = None
    while raw_video.isOpened():
        
        print(f"Frame: {frame_n} {frame_n/frame_rate}s")
        frame_n += 1
        ret, raw_frame = raw_video.read()
        if not ret:
            break
        
        rgb = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2RGB)
        
        color_frame = None
        if color_video is not None:
            ret, color_frame = color_video.read()
            color_frame = cv2.cvtColor(color_frame, cv2.COLOR_BGR2RGB)
            
            assert color_frame.shape == rgb.shape, "color image and depth image need to have same width and height" #potential BUG here with mono depth videos
        else:
            color_frame = rgb

        # Decode video depth
        depth = np.zeros((frame_height, frame_width), dtype=np.uint32)
        depth_unit = depth.view(np.uint8).reshape((frame_height, frame_width, 4))
        depth_unit[..., 3] = ((rgb[..., 0].astype(np.uint32) + rgb[..., 1]).astype(np.uint32) / 2)
        depth_unit[..., 2] = rgb[..., 2]
        depth = depth.astype(np.float32)/((255**4)/MODEL_maxOUTPUT_depth)
        
        
        if transformations is None and args.touchly1: #Fast path we can skip the full render pass
            depth8bit = np.rint(np.minimum(depth, args.touchly_max_depth)*(255/args.touchly_max_depth)).astype(np.uint8)
            touchly_depth = np.repeat(depth8bit[..., np.newaxis], 3, axis=-1)
            touchly_depth = 255 - touchly_depth #Touchly uses reverse depth
            out_image = cv2.vconcat([color_frame, touchly_depth])
        else:
            
            bg_color = np.array([0.0, 0.0, 0.0])
            if infill_mask_video is not None:
                bg_color = np.array([0.0, 1.0, 0.0])
                bg_color_infill_detect = np.array([0, 255, 0], dtype=np.uint8)
                
            
            
            if transformations is not None:
                transform_to_zero = np.array(transformations[frame_n-1])
            else:
                transform_to_zero = np.eye(4)
                
            mesh, used_indices = depth_map_tools.get_mesh_from_depth_map(depth, cam_matrix, color_frame, last_mesh, remove_edges = (args.infill_mask | args.remove_edges))
            last_mesh = mesh
            
            
            if transformations is not None:
                mesh.transform(transform_to_zero)
            
            if mask_video is not None:
                
                ret, mask_frame = mask_video.read()
                mask_img = np.array(cv2.cvtColor(mask_frame, cv2.COLOR_BGR2GRAY))
                
                #find all black pixels 
                mask_img1d = mask_img.reshape(-1)
                bg_mask = np.where(mask_img1d < 128)[0]
                
                # intersect the mask pixels with the pizels that are not edges
                points_2_keep = np.intersect1d(used_indices, bg_mask)
                
                new_points = np.asarray(mesh.vertices)[points_2_keep]
                new_colors = np.asarray(mesh.vertex_colors)[points_2_keep]
                
                
                bg_points  = np.concatenate((bg_points, new_points), axis=0)
                bg_point_colors  = np.concatenate((bg_point_colors, new_colors), axis=0)
                
                bg_cloud = depth_map_tools.pts_2_pcd(bg_points, bg_point_colors)
                
                #clear up the point clouds every so often
                if frame_n % 10 == 0:
                    print("clearing up pointcloud")
                    
                    # perspective_aware_down_sample makes sense when you are looking in the same direction, techically a normal down_sample function would be better. But it is to slow.
                    bg_cloud = depth_map_tools.perspective_aware_down_sample(bg_cloud, 0.003)#1 cubic cm
                
                    bg_points  = np.asarray(bg_cloud.points)
                    bg_point_colors = np.asarray(bg_cloud.colors)
                    bg_cloud = copy.deepcopy(bg_cloud)
                    
                
                
            if args.save_background:
                if args.max_frames < frame_n and args.max_frames != -1: #We ceed to check this here so that the continue dont skip the check
                    break
                continue
                
            
            
            #Only render the background
            if args.mask_video is not None:
                mesh = bg_cloud
                
            if args.touchly1:
                color_transformed, touchly_depth = depth_map_tools.render([mesh], render_cam_matrix, -2, bg_color = bg_color)
                color_transformed = (color_transformed*255).astype(np.uint8)
                
                
                touchly_depth8bit = np.rint(np.minimum(touchly_depth, args.touchly_max_depth)*(255/args.touchly_max_depth)).astype(np.uint8)
                touchly_depth8bit[touchly_depth8bit == 0] = 255 # Any pixel at zero depth needs to move back as it is part of the render viewport background and not the mesh
                touchly_depth8bit = 255 - touchly_depth8bit #Touchly uses reverse depth
                touchly_depth = np.repeat(touchly_depth8bit[..., np.newaxis], 3, axis=-1)
                
                out_image = cv2.vconcat([color_transformed, touchly_depth])
                
                if infill_mask_video is not None:
                    bg_mask = np.all(color_transformed == bg_color_infill_detect, axis=-1)
                    img_mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
                    img_mask[bg_mask] = 255
                    
                    zero = np.zeros((frame_height, frame_width), dtype=np.uint8)
                    
                    out_mask_image = cv2.vconcat([img_mask, zero])
                    infill_mask_video.write(cv2.cvtColor(out_mask_image, cv2.COLOR_RGB2BGR))
                    
            else:
            
                #move mesh for left eye render
                mesh.translate([-left_shift, 0.0, 0.0])
                left_image = (depth_map_tools.render([mesh], render_cam_matrix, bg_color = bg_color)*255).astype(np.uint8)
                
                if infill_mask_video is not None:
                    bg_mask = np.all(left_image == bg_color_infill_detect, axis=-1)
                    left_img_mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
                    left_img_mask[bg_mask] = 255
                    
            
                touchly_left_depth = None
                #Touchly1 requires a left eye depthmap XXX use dual rendering here to speed things upp
                if args.touchly0:
                    left_depth = depth_map_tools.render([mesh], render_cam_matrix, True)
                    left_depth8bit = np.rint(np.minimum(left_depth, args.touchly_max_depth)*(255/args.touchly_max_depth)).astype(np.uint8)
                    left_depth8bit[left_depth8bit == 0] = 255 # Any pixel at zero depth needs to move back is is non rendered depth buffer(ie things on the side of the mesh)
                    left_depth8bit = 255 - left_depth8bit #Touchly uses reverse depth
                    touchly_left_depth = np.repeat(left_depth8bit[..., np.newaxis], 3, axis=-1)
            
                #Move mesh back to center
                mesh.translate([left_shift, 0.0, 0.0])
        
                #move mesh for right eye render
                mesh.translate([-right_shift, 0.0, 0.0])
                right_image = (depth_map_tools.render([mesh], render_cam_matrix, bg_color = bg_color)*255).astype(np.uint8)
                
                if infill_mask_video is not None:
                    bg_mask = np.all(right_image == bg_color_infill_detect, axis=-1)
                    right_img_mask = np.zeros((frame_height, frame_width), dtype=np.uint8)
                    right_img_mask[bg_mask] = 255
            
                imgs = [left_image, right_image]
                if touchly_left_depth is not None:
                    imgs.append(touchly_left_depth)
                
                if args.vr180:
                    for i, img in enumerate(imgs):
                        imgs[i] = convert_to_equirectangular(img, input_fov = render_fov)
            
                out_image = cv2.hconcat(imgs)
                
                
                if infill_mask_video is not None:
                    imgs = [left_img_mask, right_img_mask]
                    if touchly_left_depth is not None:
                        zero = np.zeros((frame_height, frame_width), dtype=np.uint8)
                        imgs.append(zero)
            
                    out_mask_image = cv2.hconcat(imgs)
                    infill_mask_video.write(cv2.cvtColor(out_mask_image, cv2.COLOR_RGB2BGR))
        
        
        out.write(cv2.cvtColor(out_image, cv2.COLOR_RGB2BGR))
        
        if args.max_frames < frame_n and args.max_frames != -1:
            break
        
    if args.save_background:
        np.save(args.depth_video + '_background.npy', np.array([bg_points, bg_point_colors]))
    
    raw_video.release()
    out.release()
    
    if mask_video is not None:
        mask_video.release()
    
    if infill_mask_video is not None:
        infill_mask_video.release()

