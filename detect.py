# YOLOv5 🚀 by Ultralytics, GPL-3.0 license
"""
Run inference on images, videos, directories, streams, etc.

Usage - sources:
    $ python path/to/detect.py --weights yolov5s.pt --source 0              # webcam
                                                             img.jpg        # image
                                                             vid.mp4        # video
                                                             path/          # directory
                                                             path/*.jpg     # glob
                                                             'https://youtu.be/Zgi9g1ksQHc'  # YouTube
                                                             'rtsp://example.com/media.mp4'  # RTSP, RTMP, HTTP stream

Usage - formats:
    $ python path/to/detect.py --weights yolov5s.pt                 # PyTorch
                                         yolov5s.torchscript        # TorchScript
                                         yolov5s.onnx               # ONNX Runtime or OpenCV DNN with --dnn
                                         yolov5s.xml                # OpenVINO
                                         yolov5s.engine             # TensorRT
                                         yolov5s.mlmodel            # CoreML (macOS-only)
                                         yolov5s_saved_model        # TensorFlow SavedModel
                                         yolov5s.pb                 # TensorFlow GraphDef
                                         yolov5s.tflite             # TensorFlow Lite
                                         yolov5s_edgetpu.tflite     # TensorFlow Edge TPU
"""

import argparse
import os
import sys
from pathlib import Path

import torch
import torch.backends.cudnn as cudnn

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # YOLOv5 root directory
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

from models.common import DetectMultiBackend
from utils.dataloaders import IMG_FORMATS, VID_FORMATS, LoadImages, LoadStreams
from utils.general import (LOGGER, check_file, check_img_size, check_imshow, check_requirements, colorstr, cv2,
                           increment_path, non_max_suppression, print_args, scale_coords, strip_optimizer, xywhn2xyxy, xyxy2xywh)
from utils.plots import Annotator, colors, save_one_box
from utils.torch_utils import select_device, time_sync
from new_utils.anchor_statistics import altered_yolo_nms

#Detectron imports 
#Possiveis soluções: restart connection; install versoes anteriores; clone do repositorio localmente; escrever funçao iou
from detectron2.detectron2.structures import Boxes, pairwise_iou

from torchvision.ops import batched_nms
from torchvision import transforms
import torchvision.transforms.functional as TF

import new_utils.anchor_statistics 
from new_utils.evaluation_utils import get_preprocess_ground_truth_instances, get_preprocess_pred_instances, get_matched_results, compute_nll, compute_calibration_uncertainty_errors, compute_average_precision
import json
from utils.general import xywh2xyxy 
import numpy as np
from utils.augmentations import letterbox
from new_utils.augmentations_utils import augmentation_policy
from new_utils.uncertainty_ops import remove_detections, obtain_uncertainty_statistics

@torch.no_grad()
def run(
        weights=ROOT / 'yolov5s.pt',  # model.pt path(s)
        source=ROOT / 'data/images',  # file/dir/URL/glob, 0 for webcam
        data=ROOT / 'data/coco128.yaml',  # dataset.yaml path
        imgsz=(640, 640),  # inference size (height, width)
        conf_thres=0.25,  # confidence threshold
        iou_thres=0.45,  # NMS IOU threshold
        max_det=1000,  # maximum detections per image
        device='',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
        view_img=False,  # show results
        save_txt=False,  # save results to *.txt
        save_conf=False,  # save confidences in --save-txt labels
        save_crop=False,  # save cropped prediction boxes
        nosave=False,  # do not save images/videos
        classes=None,  # filter by class: --class 0, or --class 0 2 3
        agnostic_nms=False,  # class-agnostic NMS
        augment=False,  # augmented inference
        visualize=False,  # visualize features
        update=False,  # update all models
        project=ROOT / 'runs/detect',  # save results to project/name
        name='exp',  # save results to project/name
        exist_ok=False,  # existing project/name ok, do not increment
        line_thickness=3,  # bounding box thickness (pixels)
        hide_labels=False,  # hide labels
        hide_conf=False,  # hide confidences
        half=False,  # use FP16 half-precision inference
        dnn=False,  # use OpenCV DNN for ONNX inference
):
    source = str(source)
    save_img = not nosave and not source.endswith('.txt')  # save inference images
    is_file = Path(source).suffix[1:] in (IMG_FORMATS + VID_FORMATS)
    is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
    webcam = source.isnumeric() or source.endswith('.txt') or (is_url and not is_file)
    if is_url and is_file:
        source = check_file(source)  # download

    # Directories
    save_dir = increment_path(Path(project) / name, exist_ok=exist_ok)  # increment run
    (save_dir / 'labels' if save_txt else save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    #CONFIGS FOR RUNNING
    inference_mode = True
    remove_uncertain_clusters = False
    mc_dropout = False
    test_time_augment = False
    kitti = False
    #CHANGE NAME OF EXPERIMENT
    #experiment = '/remove_uncert_SE_095_TV_33_sem_postprocess'
    experiment = '/bdd'
    if mc_dropout:
        inference_output_dir = 'code/yolov5/methods/mc_dropout'
    elif test_time_augment:
        inference_output_dir = 'code/yolov5/methods/test_time_aug'
    else:
        inference_output_dir = 'code/yolov5/methods/output_redundancy'
    inference_output_dir = inference_output_dir + experiment
    if not (os.path.exists(inference_output_dir)):
        os.makedirs(inference_output_dir)
    # Load model
    device = select_device(device)
    model = DetectMultiBackend(weights, device=device, dnn=dnn, data=data, fp16=half, mc_enabled=mc_dropout)
    stride, names, pt = model.stride, model.names, model.pt
    imgsz = check_img_size(imgsz, s=stride)  # check image size

    # Dataloader
    if webcam:
        view_img = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source, img_size=imgsz, stride=stride, auto=pt)
        bs = len(dataset)  # batch_size
    else:
        dataset = LoadImages(source, img_size=imgsz, stride=stride, auto=pt, test_time_augmentation=test_time_augment)
        bs = 1  # batch_size
    vid_path, vid_writer = [None] * bs, [None] * bs

    # Run inference
    model.warmup(imgsz=(1 if pt else bs, 3, *imgsz))  # warmup

    final_outputs_list_xywh = []
    final_outputs_list_xyxy = []
    seen, windows, dt = 0, [], [0.0, 0.0, 0.0]
    #Decide if inference mode or just metrics calculation
    if inference_mode:
        for path, im, im0s, vid_cap, s in dataset:
            t1 = time_sync()
            t2 = time_sync()
            dt[0] += t2 - t1

            # Inference
            visualize = increment_path(save_dir / Path(path).stem, mkdir=True) if visualize else False
            #MC DROPOUT
            if mc_dropout:
                im = torch.from_numpy(im).to(device)
                im = im.half() if model.fp16 else im.float()  # uint8 to fp16/32
                im /= 255  # 0 - 255 to 0.0 - 1.0
                if len(im.shape) == 3:
                    im = im[None]  # expand for batch dim
                number_runs = 5
                accumulated_predictions = torch.tensor([]).to(device)
                for runs in range(number_runs):
                    pred = model(im,augment=augment,visualize=visualize)
                    keep, output = altered_yolo_nms(pred, conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
                    pred = torch.squeeze(pred,dim=0)
                    teste = pred[keep,:]
                    accumulated_predictions = torch.cat((accumulated_predictions, pred[keep,:]))
                t3 = time_sync()
                dt[1] += t3 - t2
                original_predictions = torch.clone(accumulated_predictions)
                original_predictions = torch.unsqueeze(original_predictions,dim=0)
                accumulated_predictions[:, :4] = xywh2xyxy(accumulated_predictions[:, :4])
                accumulated_predictions[:, :4] = scale_coords(im.shape[2:], accumulated_predictions[:, :4], im0s.shape).round()
                outputs = new_utils.anchor_statistics.pre_processing_anchor_stats(accumulated_predictions)
                outputs = new_utils.anchor_statistics.compute_anchor_statistics(outputs,device,im0s,original_predictions, remove_uncertain_clusters)
                outputs = new_utils.anchor_statistics.probabilistic_detector_postprocessing(outputs,im0s)
                outputs_xywh, outputs_xyxy = new_utils.anchor_statistics.instances_to_json(outputs,dataset.count-1,kitti)
                final_outputs_list_xywh.extend(outputs_xywh)
                final_outputs_list_xyxy.extend(outputs_xyxy)
            elif test_time_augment:
                number_augments = 10
                accumulated_predictions = torch.tensor([]).to(device)
                #augmentations = transforms.Compose([transforms.ToPILImage(),
                #                                    transforms.ColorJitter(brightness=(0.4,2),contrast=(0.4,2))
                                                    #transforms.ColorJitter(contrast=(0.1,3))
                                                    #transforms.ColorJitter(brightness=(0.2,3))
                #                                    ])

                #to_PIL = transforms.ToPILImage()
                for i in range(number_augments):
                    p = Path(path)  # to Path
                    save_path = str(save_dir / p.name)  # im.jpg
                    aug_img = im0s.copy()
                    #aug_img = augmentations(aug_img)
                    #aug_img = to_PIL(aug_img)
                    #aug_img = TF.adjust_brightness(aug_img, 2)
                    aug_img = augmentation_policy(aug_img)
                    aug_img = np.array(aug_img)
                    #cv2.imwrite(save_path[0:-4] + str(i) + save_path[-4:],aug_img)
                    # Padded resize
                    img = letterbox(aug_img, new_shape=[640,640], stride=32, auto=True)[0]
                    # Convert
                    img = img.transpose((2, 0, 1))[::-1]  # HWC to CHW, BGR to RGB
                    img = np.ascontiguousarray(img)
                    im = torch.from_numpy(img).to(device)
                    im = im.half() if model.fp16 else im.float()  # uint8 to fp16/32
                    im /= 255  # 0 - 255 to 0.0 - 1.0
                    if len(im.shape) == 3:
                        im = im[None]  # expand for batch dim
                
                    pred = model(im,augment=augment,visualize=visualize)
                    keep, output = altered_yolo_nms(pred, conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
                    pred = torch.squeeze(pred,dim=0)
                    teste = pred[keep,:]
                    accumulated_predictions = torch.cat((accumulated_predictions, pred[keep,:]))
                t3 = time_sync()
                dt[1] += t3 - t2
                original_predictions = torch.clone(accumulated_predictions)
                original_predictions = torch.unsqueeze(original_predictions,dim=0)
                accumulated_predictions[:, :4] = xywh2xyxy(accumulated_predictions[:, :4])
                accumulated_predictions[:, :4] = scale_coords(im.shape[2:], accumulated_predictions[:, :4], im0s.shape).round()
                outputs = new_utils.anchor_statistics.pre_processing_anchor_stats(accumulated_predictions)
                outputs = new_utils.anchor_statistics.compute_anchor_statistics(outputs,device,im0s,original_predictions, remove_uncertain_clusters)
                outputs = new_utils.anchor_statistics.probabilistic_detector_postprocessing(outputs,im0s,kitti)
                outputs_xywh, outputs_xyxy = new_utils.anchor_statistics.instances_to_json(outputs,dataset.count-1)
                final_outputs_list_xywh.extend(outputs_xywh)
                final_outputs_list_xyxy.extend(outputs_xyxy)
            else:
                im = torch.from_numpy(im).to(device)
                im = im.half() if model.fp16 else im.float()  # uint8 to fp16/32
                im /= 255  # 0 - 255 to 0.0 - 1.0
                if len(im.shape) == 3:
                    im = im[None]  # expand for batch dim
                pred = model(im, augment=augment, visualize=visualize)
                t3 = time_sync()
                dt[1] += t3 - t2
                #########################
                #Output Redundancy
                #Convert output coordinates xywh to xyxy
                # Rescale boxes from img_size to im0 size
                pred_redundancy = torch.clone(pred)
                pred_redundancy = torch.squeeze(pred_redundancy,dim=0)
                pred_redundancy[:, :4] = xywh2xyxy(pred_redundancy[:, :4])
                pred_redundancy[:, :4] = scale_coords(im.shape[2:], pred_redundancy[:, :4], im0s.shape).round()
                # Rescale boxes from img_size to im0 size
                outputs = new_utils.anchor_statistics.pre_processing_anchor_stats(pred_redundancy)
                outputs = new_utils.anchor_statistics.compute_anchor_statistics(outputs,device,im0s,pred, remove_uncertain_clusters)
                outputs = new_utils.anchor_statistics.probabilistic_detector_postprocessing(outputs,im0s)
                outputs_xywh, outputs_xyxy = new_utils.anchor_statistics.instances_to_json(outputs,dataset.count-1,kitti)
                #https://online.stat.psu.edu/stat505/book/export/html/645
                #outputs = remove_detections(outputs)
                #FUNCTION TO REMOVE UNCERTAIN DETECTIONS
                final_outputs_list_xywh.extend(outputs_xywh)
                final_outputs_list_xyxy.extend(outputs_xyxy)
            ##############################
            # NMS
            #pred = non_max_suppression(pred, conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
            dt[2] += time_sync() - t3
            # Second-stage classifier (optional)
            # pred = utils.general.apply_classifier(pred, classifier_model, im, im0s)
            # Process predictions
            draw_clusters = True
            if draw_clusters:
                list_of_classes = []
                if kitti:
                    names = ['car','truck','person','rider','bycicle']
                else:
                    names = ['car','bus','truck','person','rider','bycicle','motorcycle']
                seen += 1
                if webcam:  # batch_size >= 1
                    p, im0, frame = path[i], im0s[i].copy(), dataset.count
                    s += f'{i}: '
                else:
                    p, im0, frame = path, im0s.copy(), getattr(dataset, 'frame', 0)
                annotator = Annotator(im0, line_width=line_thickness, example=str(names))
                p = Path(p)  # to Path
                save_path = str(save_dir / p.name)  # im.jpg
                gn = torch.tensor(im0.shape)[[1, 0, 1, 0]]  # normalization gain whwh
                txt_path = str(save_dir / 'labels' / p.stem) + ('' if dataset.mode == 'image' else f'_{frame}')  # im.txt
                imc = im0.copy() if save_crop else im0  # for save_crop
                for i, det in enumerate(outputs_xywh):  # per image
                    xyxy =  [det['bbox'][0], 
                             det['bbox'][1],
                             det['bbox'][0] + det['bbox'][2],
                             det['bbox'][1] + det['bbox'][3]]
                    c = int(det['category_id'])  # integer class
                    label = None if hide_labels else (names[c] if hide_conf else f'{names[c-1]} {det["score"]:.2f}')
                    annotator.box_label(xyxy, label, color=colors(c, True))
                    # Print results
                    list_of_classes.append(c)
                s += '%gx%g ' % im.shape[2:]  # print string
                for c in set(list_of_classes):
                    n = list_of_classes.count(c) # detections per class
                    s += f"{n} {names[int(c-1)]}{'s' * (n > 1)}, "  # add to string
            else:
                for i, det in enumerate(pred):  # per image
                    seen += 1
                    if webcam:  # batch_size >= 1
                        p, im0, frame = path[i], im0s[i].copy(), dataset.count
                        s += f'{i}: '
                    else:
                        p, im0, frame = path, im0s.copy(), getattr(dataset, 'frame', 0)
                    p = Path(p)  # to Path
                    save_path = str(save_dir / p.name)  # im.jpg
                    txt_path = str(save_dir / 'labels' / p.stem) + ('' if dataset.mode == 'image' else f'_{frame}')  # im.txt
                    s += '%gx%g ' % im.shape[2:]  # print string
                    ###########################
                    #path_to_dataset = "../../media/Data/ruimag/bdd100k/labels"
                    #preprocessed_gt_instances = get_preprocess_ground_truth_instances(path_to_dataset)
                    #annotator = Annotator(im0, line_width=line_thickness, example=str(names))
                    #teste = preprocessed_gt_instances['gt_boxes'][0][0]
                    ###for box in preprocessed_gt_instances['gt_boxes'][0]:
                    ###    annotator.box_label(box, 'popo', (0,212,187))
                    #im0 = annotator.result()
                    #cv2.imwrite(save_path, im0)
                    ###########################
                    gn = torch.tensor(im0.shape)[[1, 0, 1, 0]]  # normalization gain whwh
                    imc = im0.copy() if save_crop else im0  # for save_crop
                    annotator = Annotator(im0, line_width=line_thickness, example=str(names))
                    if len(det):
                        # Rescale boxes from img_size to im0 size
                        det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()
                        # Print results
                        for c in det[:, -1].unique():
                            n = (det[:, -1] == c).sum()  # detections per class
                            s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string
                        # Write results
                        for *xyxy, conf, cls in reversed(det):
                            if save_txt:  # Write to file
                                xywh = (xyxy2xywh(torch.tensor(xyxy).view(1, 4)) / gn).view(-1).tolist()  # normalized xywh
                                line = (cls, *xywh, conf) if save_conf else (cls, *xywh)  # label format
                                with open(f'{txt_path}.txt', 'a') as f:
                                    f.write(('%g ' * len(line)).rstrip() % line + '\n')
                            if save_img or save_crop or view_img:  # Add bbox to image
                                c = int(cls)  # integer class
                                label = None if hide_labels else (names[c] if hide_conf else f'{names[c]} {conf:.2f}')
                                annotator.box_label(xyxy, label, color=colors(c, True))
                            if save_crop:
                                save_one_box(xyxy, imc, file=save_dir / 'crops' / names[c] / f'{p.stem}.jpg', BGR=True)
            # Stream results
            im0 = annotator.result()
            if view_img:
                if p not in windows:
                    windows.append(p)
                    cv2.namedWindow(str(p), cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)  # allow window resize (Linux)
                    cv2.resizeWindow(str(p), im0.shape[1], im0.shape[0])
                cv2.imshow(str(p), im0)
                cv2.waitKey(1)  # 1 millisecond
            # Save results (image with detections)
            if save_img:
                if dataset.mode == 'image':
                    cv2.imwrite(save_path, im0)
                else:  # 'video' or 'stream'
                    if vid_path[i] != save_path:  # new video
                        vid_path[i] = save_path
                        if isinstance(vid_writer[i], cv2.VideoWriter):
                            vid_writer[i].release()  # release previous video writer
                        if vid_cap:  # video
                            fps = vid_cap.get(cv2.CAP_PROP_FPS)
                            w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        else:  # stream
                            fps, w, h = 30, im0.shape[1], im0.shape[0]
                        save_path = str(Path(save_path).with_suffix('.mp4'))  # force *.mp4 suffix on results videos
                        vid_writer[i] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
                    vid_writer[i].write(im0)
            # Print time (inference-only)
            LOGGER.info(f'{s}Done. ({t3 - t2:.3f}s)')
        
        ##################
        #SAVE INFERENCE RESULTS TO JSON
        print('xywh has ' + str(len(final_outputs_list_xywh)) + ' final predictions')
        print('xyxy has ' + str(len(final_outputs_list_xyxy)) + ' final predictions')
        with open(os.path.join(inference_output_dir, 'coco_instances_results_xywh.json'), 'w') as fp:
                json.dump(final_outputs_list_xywh, fp, indent=4,
                        separators=(',', ': '))
        
        with open(os.path.join(inference_output_dir, 'coco_instances_results_xyxy.json'), 'w') as fp:
                json.dump(final_outputs_list_xyxy, fp, indent=4,
                        separators=(',', ': '))
    
    #Compute Metrics
    if kitti:
        path_to_dataset = "../../media/Data/ruimag/kitti/object/training/label2-COCO-Format"
    else:
        path_to_dataset = "../../media/Data/ruimag/bdd100k/labels"
    preprocessed_gt_instances = get_preprocess_ground_truth_instances(path_to_dataset)
    preprocessed_pred_instances = get_preprocess_pred_instances(inference_output_dir)
    matched_results = get_matched_results(inference_output_dir, preprocessed_gt_instances, preprocessed_pred_instances)
    teste = obtain_uncertainty_statistics(matched_results)
    mAP_results, optimal_score_threshold_f1  = compute_average_precision(inference_output_dir,path_to_dataset,kitti)
    final_results_nll , final_results_per_class_nll = compute_nll(matched_results,kitti)
    final_results_calibration = compute_calibration_uncertainty_errors(matched_results,kitti)
    ########################
    if inference_mode:
        # Print results
        t = tuple(x / seen * 1E3 for x in dt)  # speeds per image
        LOGGER.info(f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS per image at shape {(1, 3, *imgsz)}' % t)
        if save_txt or save_img:
            s = f"\n{len(list(save_dir.glob('labels/*.txt')))} labels saved to {save_dir / 'labels'}" if save_txt else ''
            LOGGER.info(f"Results saved to {colorstr('bold', save_dir)}{s}")
        if update:
            strip_optimizer(weights)  # update model (to fix SourceChangeWarning)


def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', nargs='+', type=str, default=ROOT / 'yolov5s.pt', help='model path(s)')
    parser.add_argument('--source', type=str, default=ROOT / 'data/images', help='file/dir/URL/glob, 0 for webcam')
    parser.add_argument('--data', type=str, default=ROOT / 'data/coco128.yaml', help='(optional) dataset.yaml path')
    parser.add_argument('--imgsz', '--img', '--img-size', nargs='+', type=int, default=[640], help='inference size h,w')
    parser.add_argument('--conf-thres', type=float, default=0.25, help='confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.45, help='NMS IoU threshold')
    parser.add_argument('--max-det', type=int, default=1000, help='maximum detections per image')
    parser.add_argument('--device', default='', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--view-img', action='store_true', help='show results')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--save-crop', action='store_true', help='save cropped prediction boxes')
    parser.add_argument('--nosave', action='store_true', help='do not save images/videos')
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --classes 0, or --classes 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    parser.add_argument('--visualize', action='store_true', help='visualize features')
    parser.add_argument('--update', action='store_true', help='update all models')
    parser.add_argument('--project', default=ROOT / 'runs/detect', help='save results to project/name')
    parser.add_argument('--name', default='exp', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    parser.add_argument('--line-thickness', default=3, type=int, help='bounding box thickness (pixels)')
    parser.add_argument('--hide-labels', default=False, action='store_true', help='hide labels')
    parser.add_argument('--hide-conf', default=False, action='store_true', help='hide confidences')
    parser.add_argument('--half', action='store_true', help='use FP16 half-precision inference')
    parser.add_argument('--dnn', action='store_true', help='use OpenCV DNN for ONNX inference')
    opt = parser.parse_args()
    opt.imgsz *= 2 if len(opt.imgsz) == 1 else 1  # expand
    print_args(vars(opt))
    return opt


def main(opt):
    check_requirements(exclude=('tensorboard', 'thop'))
    run(**vars(opt))


if __name__ == "__main__":
    opt = parse_opt()
    main(opt)
